from flask import Flask, request, send_file, jsonify
import os
import time
import threading
import pymysql  # 新增MySQL依赖
import os
from dotenv import load_dotenv  # 可选：若使用.env文件
load_dotenv()  # 可选：加载.env文件
# ==================== 配置项 ====================
# 服务端口
SERVER_PORT = 58080
# 固定鉴权 Token
VALID_TOKEN = os.getenv("VALID_TOKEN")
# 文件根目录（历史附件存放路径）
FILE_BASE_DIR = "/root/.qq-chat-exporter/history"
# 最新更新日志文件绝对路径
UPDATE_LOG_PATH = "/root/.qq-chat-exporter/history/log/update.txt"
# 开启公网访问 0.0.0.0
LISTEN_HOST = "0.0.0.0"
# 限流配置：单IP最小请求间隔（单位：秒）
RATE_LIMIT_SECONDS = 0.5

# ========== 新增 MySQL 配置 ==========

MYSQL_CFG = {
    "host": "localhost",
    "user": os.getenv("MYSQL_USER"),
    "password": os.getenv("MYSQL_PASSWORD"),
    "database": os.getenv("MYSQL_DB")
}

# 初始化Flask
app = Flask(__name__)

# ==================== 限流逻辑 ====================
# 存储每个IP上次请求的时间戳
_ip_last_request = {}
# 线程锁，保证并发下时间记录安全
_rate_lock = threading.Lock()

@app.before_request
def global_rate_limit():
    """全局请求限流：单IP请求间隔不小于设定值"""
    client_ip = request.remote_addr
    now = time.time()
    
    with _rate_lock:
        last_time = _ip_last_request.get(client_ip, 0)
        # 检查请求间隔
        if now - last_time < RATE_LIMIT_SECONDS:
            return jsonify({"code": 429, "msg": "请求过于频繁，请1秒后重试"}), 429
        # 更新本次请求时间
        _ip_last_request[client_ip] = now

# ==================== 公共方法 ====================
# 统一错误返回
def make_resp(code: int, msg: str):
    return jsonify({"code": code, "msg": msg}), code

# ==================== 原有接口 ====================
@app.route("/getFile", methods=["GET"])
def get_file():
    # 1. 校验Token
    req_token = request.args.get("token", "")
    if req_token != VALID_TOKEN:
        return make_resp(401, "Token Invailed")

    # 2. 获取 file_url 参数
    file_url = request.args.get("file_url", "").strip()
    if not file_url:
        return make_resp(400, "参数缺失：请传入 file_url")

    # 3. 安全校验：防止路径穿越攻击（../ 逃逸目录）
    if ".." in file_url or file_url.startswith("/") or file_url.startswith("\\"):
        return make_resp(403, "Invaild path,access denied")

    # 4. 拼接文件绝对路径
    file_abs_path = os.path.abspath(os.path.join(FILE_BASE_DIR, file_url))

    # 二次校验：确保文件在指定根目录内
    if not file_abs_path.startswith(os.path.abspath(FILE_BASE_DIR)):
        return make_resp(403, "访问路径超出授权目录")

    # 5. 判断文件是否存在 & 是否为普通文件
    if not os.path.isfile(file_abs_path):
        return make_resp(404, "Cannot find files")

    # 6. 返回文件流
    try:
        return send_file(file_abs_path, as_attachment=True)
    except Exception as e:
        return make_resp(500, f"Fail to acess the file:{str(e)}")

@app.route("/latest", methods=["GET"])
def get_latest_log():
    """无需token，直接返回最新更新日志文本，浏览器可直接查看"""
    if not os.path.isfile(UPDATE_LOG_PATH):
        return make_resp(404, "Cannot find latest log")
    try:
        # 以纯文本形式返回，指定UTF-8编码避免中文乱码
        return send_file(
            UPDATE_LOG_PATH,
            mimetype="text/plain; charset=utf-8",
            as_attachment=False
        )
    except Exception as e:
        return make_resp(500, f"Failed to access latest log:{str(e)}")

# ==================== 【新增】查询群名接口 /groupname ====================
@app.route("/groupname", methods=["GET"])
def query_group_name():
    # 1. 校验Token（和原有保持一致）
    req_token = request.args.get("token", "")
    if req_token != VALID_TOKEN:
        return make_resp(401, "Token Invalid")

    try:
        # 2. 连接MySQL
        conn = pymysql.connect(
            host=MYSQL_CFG["host"],
            user=MYSQL_CFG["user"],
            password=MYSQL_CFG["password"],
            database=MYSQL_CFG["database"],
            charset="utf8mb4"
        )
        cursor = conn.cursor()

        # 3. 执行SQL
        sql = "SELECT DISTINCT group_name FROM qq_chat_records;"
        cursor.execute(sql)
        rows = cursor.fetchall()

        # 4. 拼接纯文本结果（每行一个群名）
        result_text = "\n".join([row[0] for row in rows if row[0]])

        # 5. 关闭连接
        cursor.close()
        conn.close()

        # 6. 以纯文本返回
        return result_text, 200, {"Content-Type": "text/plain; charset=utf-8"}

    except Exception as e:
        return make_resp(500, f"Failed to link sql:{str(e)}")

if __name__ == "__main__":
    # 关闭调试模式（生产环境必须关闭 debug）
    app.run(host=LISTEN_HOST, port=SERVER_PORT, debug=False)