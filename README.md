# UniMap Auto Inbound

从 Google Sheet 读取单号，打开 [UniUni Dispatch](https://dispatch.uniuni.com/main)，在 **Edit Order → Operation** 里按当前状态连续 Next Transition，直到 **215**。

## 状态路径

每一步统一顺序：**Next Transition → Operation Location=`NJ Warehouse` → Submit**。

| 当前状态 | Next Transition | 额外字段 | 目标 |
|---|---|---|---|
| 190 / **255** | gateway processing | Operation Location | 199 |
| 199 / **195** / **1910** | parcel scan / parcel scanned | Operation Location | 200 |
| 200 | wrong address cfm in dispatch | Operation Location | 212 |
| 212 | deliver parcel apt | Operation Location；Submit 后弹窗 fail reason=`parcel damaged` | 211 |
| 211 | send parcel to storage | Operation Location + Network Node=`WH- JFK-005` | 213 |
| 213 | parcel abandon | Operation Location | 215 |

只要当前状态是上表任一节点，就从该节点按同一路径继续走到 215。

**安全策略：** 遇到未讲解过的状态码，或某步执行后实际状态与表中目标不一致，或找不到对应 Next Transition / 额外字段选项，会立即停止，不再处理后续单号。

## 安装（已在本机完成依赖安装的可跳过）

```bash
cd ~/unimap-auto-inbound
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 读取 Google Sheet（三选一）

默认 Sheet：`1yrR83W15kKevye87ksYnELUY68i_j4_kIIY_gu0bAWU`，读 **A 列**，跳过表头。

1. **Service Account（推荐）**  
   - JSON 放到 `credentials/unimap-put-in-storage-google-service-account.json`  
   - 把 Sheet 共享给该账号的 `client_email`（Viewer）

2. **本地 CSV（最快上手）**  
   - 从 Sheet 导出 CSV，然后：
   ```bash
   LOCAL_CSV_FILE=./orders.csv ./run.sh
   ```

3. **公开链接**：Sheet 设为「知道链接的任何人可查看」

## 推荐用法：Sheet 菜单 + 本机监听

Apps Script 负责菜单确认；本机负责操作 UniUni（Google 脚本无法替你点网页）。

1. 安装 Sheet 菜单：见 [`apps-script/README.md`](apps-script/README.md)（粘贴 `Code.gs`）  
2. 配置服务账号 JSON，并把 Sheet **共享为编辑者**给 `client_email`  
3. 本机保持运行：

```bash
cd ~/unimap-auto-inbound
./run.sh --watch
```

4. 在 Sheet **先选中**要从哪一行 A 列单号开始  
5. 菜单 **一键入库 → 从选中单号开始**  
   - 会把**该行起直到空行**的所有 A 列单号写入可见页 **「入库队列」**  
   - 本机按队列顺序搜索入库，到 **215** 后打勾并继续下一单（浏览器不关）  
   - 要中止：菜单 **停止连续执行**（当前单跑完，剩余 pending 标 cancelled）  
   - 本批结束后 **terminal / 浏览器都不用关**：再选单号点菜单即可

## 其它命令

```bash
./run.sh --login-only          # 只登录
./run.sh --next                # 不经菜单，终端确认后处理下一单
./run.sh --order YOUR_ORDER_NO
./run.sh --limit 3
./run.sh --dry-run
```

登录会话保存在 `.browser-profile/`。

## 相关项目

EWR 妥投 / DSP Tools 已拆到独立目录：`~/delivery-pivot`
