/**
 * UniMap 入库闭环 — 绑定到你的 Google Sheet
 *
 * 安装：
 * 1. 打开 Sheet → 扩展程序 → Apps Script
 * 2. 删除默认代码，粘贴本文件全部内容
 * 3. 保存后刷新 Sheet，顶部菜单出现「一键入库」
 * 4. 本机保持运行: ./run.sh --watch （负责真正操作 UniUni）
 *
 * 流程：
 * - A 列 = 单号，B 列 = 完成后勾选
 * - 选中起始行 →「从选中单号开始」
 * - 队列写入：从该行起 A 列连续单号，直到空行（整批可见于「入库队列」页）
 * - 本机按队列顺序做到 215；「停止连续执行」中止后续 pending
 */

var QUEUE_SHEET = "入库队列";
var ORDER_COL = 1; // A
var DONE_COL = 2; // B
var HEADER_ROWS = 1;

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("一键入库")
    .addItem("从选中单号开始", "startFromSelection")
    .addItem("停止连续执行", "requestStop")
    .addSeparator()
    .addItem("查看队列状态", "showQueueStatus")
    .addItem("打开队列页", "openQueueSheet")
    .addToUi();
}

function isChecked_(value) {
  return (
    value === true ||
    value === "TRUE" ||
    value === "true" ||
    value === 1 ||
    value === "✓"
  );
}

/** 选中行校验；返回 {row, orderNo} */
function getSelectedStart_() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var range = sheet.getActiveRange();
  if (!range) {
    return { error: "请先用鼠标选中 A 列中的一个单号单元格。" };
  }

  var row = range.getRow();
  if (row <= HEADER_ROWS) {
    return { error: "请选中表头以下的数据行（不要选第 1 行表头）。" };
  }

  var orderNo = String(sheet.getRange(row, ORDER_COL).getValue() || "").trim();
  if (!orderNo) {
    return { error: "A" + row + " 为空行。请选中有单号的单元格。" };
  }

  return { row: row, orderNo: orderNo, sheet: sheet };
}

/**
 * 从 startRow 起读取 A 列，直到遇到空行。
 * 返回 [{row, orderNo, alreadyDone}, ...]
 */
function collectOrdersUntilBlank_(sheet, startRow) {
  var lastRow = Math.max(sheet.getLastRow(), startRow);
  var values = sheet.getRange(startRow, ORDER_COL, lastRow, DONE_COL).getValues();
  var list = [];

  for (var i = 0; i < values.length; i++) {
    var orderNo = String(values[i][0] || "").trim();
    if (!orderNo) break; // 空行 = 本批结束
    list.push({
      row: startRow + i,
      orderNo: orderNo,
      alreadyDone: isChecked_(values[i][1]),
    });
  }
  return list;
}

function batchBusy_(q) {
  var flag = String(q.getRange("G1").getValue() || "").toLowerCase();
  return flag === "pending" || flag === "running";
}

function startFromSelection() {
  var ui = SpreadsheetApp.getUi();
  var start = getSelectedStart_();
  if (start.error) {
    ui.alert("已停止", start.error, ui.ButtonSet.OK);
    return;
  }

  var orders = collectOrdersUntilBlank_(start.sheet, start.row);
  if (!orders.length) {
    ui.alert("已停止", "从第 " + start.row + " 行起没有可读单号。", ui.ButtonSet.OK);
    return;
  }

  var preview = orders
    .slice(0, 8)
    .map(function (o) {
      return o.orderNo + (o.alreadyDone ? "（已勾选）" : "");
    })
    .join("\n");
  if (orders.length > 8) preview += "\n… 共 " + orders.length + " 单";

  var msg =
    "从 " +
    start.orderNo +
    " 开始，直到空行，共 " +
    orders.length +
    " 单：\n\n" +
    preview +
    "\n\n确认写入「入库队列」并由本机按序执行？";
  if (ui.alert("确认开始", msg, ui.ButtonSet.OK_CANCEL) !== ui.Button.OK) {
    ui.alert("已取消");
    return;
  }

  var q = ensureQueueSheet_();

  if (batchBusy_(q)) {
    var overwrite = ui.alert(
      "队列占用中",
      "已有一批任务在 pending/running。\n是否清空并换成当前这 " +
        orders.length +
        " 单？",
      ui.ButtonSet.YES_NO
    );
    if (overwrite !== ui.Button.YES) return;
  }

  // 清空旧队列数据（保留表头）
  var oldLast = q.getLastRow();
  if (oldLast >= 2) {
    q.getRange(2, 1, oldLast, 5).clearContent();
  }

  var now = new Date();
  var rows = orders.map(function (o) {
    return [
      o.orderNo,
      o.row,
      o.alreadyDone ? "skipped" : "pending",
      now,
      o.alreadyDone ? "already checked in B column" : "queued until blank row",
    ];
  });
  q.getRange(2, 1, rows.length, 5).setValues(rows);

  // G1 = 批次状态；H1 = 说明
  q.getRange("G1").setValue("pending");
  q.getRange("H1").setValue(
    "from row " + start.row + ", count=" + orders.length + ", at " + now.toISOString()
  );

  var pendingCount = orders.filter(function (o) {
    return !o.alreadyDone;
  }).length;

  SpreadsheetApp.getActiveSpreadsheet().toast(
    "已排队 " + orders.length + " 单（待跑 " + pendingCount + "）。见「入库队列」页。",
    "一键入库",
    8
  );

  ui.alert(
    "已写入队列",
    "队列页：入库队列\n" +
      "起始：" +
      start.orderNo +
      "（第 " +
      start.row +
      " 行）\n" +
      "总计：" +
      orders.length +
      " 单（到空行为止）\n" +
      "待执行：" +
      pendingCount +
      " 单\n" +
      "已勾选跳过：" +
      (orders.length - pendingCount) +
      " 单\n\n" +
      "请保持本机「一键入库-启动」运行；它会按队列顺序搜索并入库。\n" +
      "要中止：菜单 → 停止连续执行。",
    ui.ButtonSet.OK
  );

  // 方便查看：跳到队列页
  q.activate();
}

function requestStop() {
  var ui = SpreadsheetApp.getUi();
  var q = ensureQueueSheet_();
  var flag = String(q.getRange("G1").getValue() || "").toLowerCase();

  if (flag !== "pending" && flag !== "running") {
    ui.alert(
      "无需停止",
      "当前没有等待/执行中的批次（G1=" + (flag || "空") + "）。",
      ui.ButtonSet.OK
    );
    return;
  }

  if (
    ui.alert(
      "确认停止",
      "将停止连续执行：\n" +
        "• 当前正在跑的一单会尽量跑完\n" +
        "• 队列里剩余 pending 会标为 cancelled\n" +
        "• 本机 terminal 不用关，之后可重新选单号开始",
      ui.ButtonSet.YES_NO
    ) !== ui.Button.YES
  ) {
    return;
  }

  q.getRange("G1").setValue("stop");
  q.getRange("H1").setValue("stop requested at " + new Date().toISOString());

  // 立刻把还没跑的 pending 标成 cancelled（running 留给本机结束后处理）
  var last = q.getLastRow();
  if (last >= 2) {
    var statuses = q.getRange(2, 3, last, 3).getValues();
    var now = new Date();
    for (var i = 0; i < statuses.length; i++) {
      if (String(statuses[i][0] || "").toLowerCase() === "pending") {
        q.getRange(i + 2, 3).setValue("cancelled");
        q.getRange(i + 2, 4).setValue(now);
        q.getRange(i + 2, 5).setValue("cancelled by stop");
      }
    }
  }

  SpreadsheetApp.getActiveSpreadsheet().toast("已发送停止指令", "一键入库", 6);
  ui.alert(
    "已请求停止",
    "批次状态 = stop。\n当前单结束后不再继续。\n然后选中新单号 →「从选中单号开始」即可再跑。",
    ui.ButtonSet.OK
  );
}

function showQueueStatus() {
  var q = ensureQueueSheet_();
  var flag = q.getRange("G1").getValue();
  var note = q.getRange("H1").getValue();
  var last = q.getLastRow();
  var counts = {
    pending: 0,
    running: 0,
    done: 0,
    error: 0,
    skipped: 0,
    cancelled: 0,
    other: 0,
  };
  var lines = [];

  if (last >= 2) {
    var data = q.getRange(2, 1, last, 3).getValues();
    for (var i = 0; i < data.length; i++) {
      var orderNo = data[i][0];
      var status = String(data[i][2] || "").toLowerCase();
      if (!orderNo) continue;
      if (counts.hasOwnProperty(status)) counts[status]++;
      else counts.other++;
      if (i < 15) lines.push(orderNo + " → " + status);
    }
    if (data.length > 15) lines.push("… 共 " + data.length + " 行");
  }

  SpreadsheetApp.getUi().alert(
    "队列状态",
    "批次 G1: " +
      (flag || "-") +
      "\n说明: " +
      (note || "-") +
      "\n\npending=" +
      counts.pending +
      " running=" +
      counts.running +
      " done=" +
      counts.done +
      "\nerror=" +
      counts.error +
      " skipped=" +
      counts.skipped +
      " cancelled=" +
      counts.cancelled +
      "\n\n" +
      (lines.length ? lines.join("\n") : "(队列为空)"),
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

function openQueueSheet() {
  ensureQueueSheet_().activate();
}

function ensureQueueSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var q = ss.getSheetByName(QUEUE_SHEET);
  // 兼容旧隐藏页
  if (!q) q = ss.getSheetByName("_UnimapQueue");
  if (!q) {
    q = ss.insertSheet(QUEUE_SHEET);
  } else if (q.getName() !== QUEUE_SHEET) {
    q.setName(QUEUE_SHEET);
  }

  q.showSheet();
  if (String(q.getRange("A1").getValue() || "") !== "order_no") {
    q.getRange("A1:E1").setValues([["order_no", "row", "status", "updated_at", "note"]]);
    q.getRange("A1:E1").setFontWeight("bold");
  }
  if (!q.getRange("F1").getValue()) {
    q.getRange("F1").setValue("batch→");
  }
  if (!q.getRange("G1").getValue()) {
    q.getRange("G1").setValue("idle");
  }
  return q;
}
