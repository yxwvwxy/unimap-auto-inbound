# Google Sheet 菜单：一键入库

Apps Script **不能**直接操作 UniUni 网页（Microsoft 登录 + 页面点击），所以分成两段：

1. **Sheet 菜单**：选下一单、弹窗确认、写入队列、勾选完成后的 B 列由本机回写  
2. **本机 `./run.sh --watch`**：登录 UniUni，监听队列并真正跑闭环

## 一次性安装 Apps Script

1. 打开你的 Sheet  
   https://docs.google.com/spreadsheets/d/1yrR83W15kKevye87ksYnELUY68i_j4_kIIY_gu0bAWU/edit  
2. **扩展程序 → Apps Script**  
3. 把 `Code.gs` 全部粘贴进去，保存  
4. 回到 Sheet **刷新**，顶部应出现菜单 **「一键入库」**

## 一次性配置本机读/写 Sheet

1. [Google Cloud](https://console.cloud.google.com/) 建项目 → 启用 **Google Sheets API**  
2. 创建 **服务账号** → 下载 JSON  
3. 保存为：

```text
~/unimap-auto-inbound/credentials/unimap-put-in-storage-google-service-account.json
```

4. 打开 JSON，找到 `client_email`（形如 `xxx@....iam.gserviceaccount.com`）  
5. 把 Sheet **共享**给这个邮箱，权限选 **编辑者**

## 日常使用

终端（保持开着）：

```bash
cd ~/unimap-auto-inbound
./run.sh --watch
```

浏览器里完成 Microsoft 登录后，终端按 Enter。

然后在 Sheet：

1. 用鼠标选中要开始的那一行 A 列单号  
2. **一键入库 → 从选中单号开始**  
3. 确认后：从该行起 **直到空行** 的全部单号写入 **「入库队列」** 页（可打开查看）  
4. 本机按队列顺序做到 215 → 打勾 → 立刻搜下一单（Chrome 不关）  
5. 要停：菜单 **停止连续执行**  
6. 本批结束后不用关 terminal：再选单号点菜单即可

## 不经过菜单时

```bash
./run.sh --next          # 终端确认后处理最小未勾选行
```
