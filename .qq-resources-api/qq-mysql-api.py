# 【必须放在最顶部】gevent协程补丁，解决IO阻塞卡死
from gevent import monkey
monkey.patch_all()
from flask import Flask, request, render_template_string
import pymysql
import time
import threading
import json
from datetime import datetime, date
import re
# 导入外部分离文件（新增build_checkforclass_sql）
from sql_mapping import get_full_query_mapping, build_checkforclass_sql
from admin_view import (
    mask_token_in_log_text,
    write_request_log,
    read_today_logs,
    clear_today_log,
    LOG_HTML_TPL,
    SECRET_INPUT_TPL
)
# ==================== 全局配置 ====================
import os
from dotenv import load_dotenv  # 可选：若使用.env文件
load_dotenv()  # 可选：加载.env文件
XQM_PARAM = os.getenv("XQM_PARAM")
TOKEN_HEADER = os.getenv("TOKEN_HEADER")
LOG_PAGE_SECRET = os.getenv("LOG_PAGE_SECRET")
SERVER_PORT = int(os.getenv("SERVER_PORT"))
VALID_TOKEN = os.getenv("VALID_TOKEN")
LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
# MySQL配置
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "user": os.getenv("MYSQL_USER"),
    "password": os.getenv("MYSQL_PASSWORD"),
    "database": os.getenv("MYSQL_DB"),
    "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
    "port": int(os.getenv("MYSQL_PORT", 3306)),
    "cursorclass": pymysql.cursors.DictCursor
}
# 安全配置
SECURITY_CONFIG = {
    "only_select": os.getenv("SECURITY_ONLY_SELECT", "True").lower() == "true",
    "max_return_rows": int(os.getenv("SECURITY_MAX_ROWS", 5000)),
    "rate_limit_seconds": int(os.getenv("RATE_LIMIT_SECONDS", 1))
}
# ==================== JSON时间编码器 ====================
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(obj, date):
            return obj.strftime("%Y-%m-%d")
        return super().default(obj)
# ==================== Flask初始化 ====================
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["JSONIFY_MIMETYPE"] = "application/json; charset=utf-8"
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False
_ip_last_request = {}
_rate_lock = threading.Lock()
_thread_local = threading.local()
# ==================== 工具函数 ====================
def rate_limit_check():
    client_ip = request.remote_addr
    now = time.time()
    with _rate_lock:
        last_time = _ip_last_request.get(client_ip, 0)
        if now - last_time < SECURITY_CONFIG["rate_limit_seconds"]:
            return False
        _ip_last_request[client_ip] = now
    return True

def sql_safety_check(sql: str) -> tuple:
    sql_stripped = sql.strip()
    if not sql_stripped:
        return False, "SQL语句不能为空"
    if ";" in sql_stripped:
        return False, "禁止包含分号的多语句查询"
    sql_upper = sql.strip().upper()
    if SECURITY_CONFIG["only_select"] and not sql_upper.startswith("SELECT"):
        return False, "仅允许执行SELECT查询语句"
    danger_keywords = [
        "DELETE", "UPDATE", "INSERT", "DROP", "ALTER", "CREATE",
        "TRUNCATE", "EXEC", "EXECUTE", "UNION", "INTO", "SLEEP",
        "BENCHMARK", "LOAD_FILE", "INFORMATION_SCHEMA"
    ]
    for kw in danger_keywords:
        if kw in sql_upper:
            return False, f"SQL包含禁止关键字：{kw}"
    return True, "校验通过"

def make_resp(code: int, msg: str, resp_ms: float, data=None, total=0):
    resp = {
        "code": code,
        "msg": msg,
        "total": total,
        "response_time": round(resp_ms, 2)
    }
    if data is not None:
        resp["data"] = data
    try:
        resp_json = json.dumps(resp, ensure_ascii=False, indent=None, cls=DateTimeEncoder)
        status = code
    except Exception as e:
        fallback = {
            "code": 500,
            "msg": f"数据序列化异常：{str(e)}",
            "total": 0,
            "response_time": round(resp_ms, 2)
        }
        resp_json = json.dumps(fallback, ensure_ascii=False, indent=None)
        status = 500
    return app.response_class(resp_json, status=status, mimetype="application/json; charset=utf-8")
# ==================== 前置限流中间件 ====================
@app.before_request
def before_request_handler():
    _thread_local.start_ts = time.time()
    if not rate_limit_check():
        now = time.time()
        cost_ms = (now - _thread_local.start_ts) * 1000
        log_data = {
            "ip": request.remote_addr,
            "full_url": request.url,
            "token_header": request.headers.get(TOKEN_HEADER, ""),
            "token_url": request.args.get("token", ""),
            "XQM": request.args.get(XQM_PARAM, ""),
            "input_sql": request.args.get("sql", ""),
            "final_sql": "",
            "code": 429,
            "cost_ms": round(cost_ms,2),
            "err": "请求过于频繁"
        }
        write_request_log(log_data)
        return make_resp(429, f"请求过于频繁，请{SECURITY_CONFIG['rate_limit_seconds']}秒", cost_ms)
# ==================== 运维日志页面接口 ====================
@app.route("/logmanage", methods=["GET"])
def log_manage_page():
    secret = request.args.get("secret", "")
    if secret != LOG_PAGE_SECRET:
        return SECRET_INPUT_TPL
    today_str = datetime.now().strftime("%Y-%m-%d")
    raw_log = read_today_logs()
    masked_log = mask_token_in_log_text(raw_log)
    return render_template_string(LOG_HTML_TPL, today=today_str, logs=masked_log, secret=LOG_PAGE_SECRET)

@app.route("/clear_log", methods=["GET"])
def api_clear_log():
    secret = request.args.get("secret", "")
    if secret != LOG_PAGE_SECRET:
        return json.dumps({"code":403,"msg":"缺少或无效日志访问密钥"}, ensure_ascii=False),400,{"Content-Type":"application/json;charset=utf-8"}
    try:
        clear_today_log()
        return json.dumps({"code":200,"msg":"今日日志已全部清空"}, ensure_ascii=False), 200, {"Content-Type":"application/json;charset=utf-8"}
    except Exception as e:
        return json.dumps({"code":500,"msg":f"清空失败：{str(e)}"}, ensure_ascii=False), 500, {"Content-Type":"application/json;charset=utf-8"}
# ==================== 核心查询接口 ====================
@app.route("/query", methods=["GET", "POST"])
def mysql_query():
    start = _thread_local.start_ts
    client_ip = request.remote_addr
    input_sql = ""
    xqm_val = ""
    token_header = request.headers.get(TOKEN_HEADER, "").strip()
    token_url = request.args.get("token", "").strip()
    req_token = token_header if token_header else token_url
    if request.method == "GET":
        input_sql = request.args.get("sql", "").strip()
        xqm_val = request.args.get(XQM_PARAM, "").strip()
    else:
        if request.is_json:
            input_sql = request.json.get("sql", "").strip()
            xqm_val = request.json.get(XQM_PARAM, "").strip()
        else:
            input_sql = request.form.get("sql", "").strip()
            xqm_val = request.form.get(XQM_PARAM, "").strip()
    log_data = {
        "ip": client_ip,
        "full_url": request.url,
        "token_header": token_header,
        "token_url": token_url,
        "XQM": xqm_val,
        "input_sql": input_sql,
        "final_sql": "",
        "code": 200,
        "cost_ms": 0,
        "err": ""
    }
    if req_token != VALID_TOKEN:
        cost_ms = (time.time() - start) * 1000
        log_data["code"] = 401
        log_data["cost_ms"] = round(cost_ms,2)
        log_data["err"] = "Token缺失或无效（支持X-Api-Token请求头 / url token参数）"
        write_request_log(log_data)
        return make_resp(401, "Token无效，请在Header填X-Api-Token或URL携带token参数", cost_ms)
    final_sql = input_sql
    full_mapping = get_full_query_mapping()

    # 新增：优先处理 XQM=checkforclass 教室模糊查询逻辑
    if xqm_val == "checkforclass":
        class_arg = ""
        if request.method == "GET":
            class_arg = request.args.get("class", "").strip()
        else:
            if request.is_json:
                class_arg = request.json.get("class", "").strip()
            else:
                class_arg = request.form.get("class", "").strip()
        if not class_arg:
            cost_ms = (time.time() - start) * 1000
            log_data["code"] = 400
            log_data["cost_ms"] = round(cost_ms,2)
            log_data["err"] = "XQM=checkforclass 必须传入class参数"
            write_request_log(log_data)
            return make_resp(400, "XQM=checkforclass 必须传入class参数", cost_ms)
        # 生成教室模糊查询SQL，跳过下方XQM存在校验
        final_sql = build_checkforclass_sql(class_arg)
    else:
        # 原有固定XQM逻辑完全保留
        if not final_sql:
            if not xqm_val:
                cost_ms = (time.time() - start) * 1000
                log_data["code"] = 400
                log_data["cost_ms"] = round(cost_ms,2)
                log_data["err"] = "无sql且XQM为空"
                write_request_log(log_data)
                return make_resp(400, "未传入sql，需携带sql或XQM参数", cost_ms)
            if xqm_val not in full_mapping:
                cost_ms = (time.time() - start) * 1000
                support = ",".join(full_mapping.keys())
                log_data["code"] = 403
                log_data["cost_ms"] = round(cost_ms,2)
                log_data["err"] = f"XQM非法，支持：{support}"
                write_request_log(log_data)
                return make_resp(403, f"XQM仅支持：{support}", cost_ms)
            final_sql = full_mapping[xqm_val]

    log_data["final_sql"] = final_sql
    safe, msg = sql_safety_check(final_sql)
    if not safe:
        cost_ms = (time.time() - start) * 1000
        log_data["code"] = 403
        log_data["cost_ms"] = round(cost_ms,2)
        log_data["err"] = msg
        write_request_log(log_data)
        return make_resp(403, f"SQL校验失败：{msg}", cost_ms)
    try:
        with pymysql.connect(**MYSQL_CONFIG) as conn:
            with conn.cursor() as cursor:
                cursor.execute(final_sql)
                result = cursor.fetchmany(SECURITY_CONFIG["max_return_rows"])
        total = len(result)
        resp_msg = "查询成功" if total < SECURITY_CONFIG["max_return_rows"] else f"查询成功，最多返回{SECURITY_CONFIG['max_return_rows']}条"
        cost_ms = (time.time() - start) * 1000
        log_data["cost_ms"] = round(cost_ms,2)
        write_request_log(log_data)
        return make_resp(200, resp_msg, cost_ms, data=result, total=total)
    except pymysql.Error as e:
        cost_ms = (time.time() - start) * 1000
        log_data["code"] = 500
        log_data["cost_ms"] = round(cost_ms,2)
        log_data["err"] = f"数据库异常:{str(e)}"
        write_request_log(log_data)
        return make_resp(500, f"数据库查询失败：{str(e)}", cost_ms)
    except Exception as e:
        cost_ms = (time.time() - start) * 1000
        log_data["code"] = 500
        log_data["cost_ms"] = round(cost_ms,2)
        log_data["err"] = f"服务异常:{str(e)}"
        write_request_log(log_data)
        return make_resp(500, f"服务异常：{str(e)}", cost_ms)
# 健康检查
@app.route("/health", methods=["GET"])
def health_check():
    cost_ms = (time.time() - _thread_local.start_ts) * 1000
    log_data = {
        "ip": request.remote_addr,
        "full_url": request.url,
        "token_header": request.headers.get(TOKEN_HEADER, ""),
        "token_url": request.args.get("token", ""),
        "XQM": "",
        "input_sql": "",
        "final_sql": "",
        "code": 200,
        "cost_ms": round(cost_ms,2),
        "err": ""
    }
    write_request_log(log_data)
    return make_resp(200, "[INFO]服务运行正常", cost_ms)
if __name__ == "__main__":
    app.run(host=LISTEN_HOST, port=SERVER_PORT, debug=False)