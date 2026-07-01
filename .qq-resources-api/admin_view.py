# admin_view.py
import os
import json
import re
from datetime import datetime

# ==================== 日志脱敏工具 ====================
def mask_token_in_log_text(log_text: str) -> str:
    pattern_json = r'"(token_header|token_url)":\s*"[^"]+"'
    masked = re.sub(pattern_json, r'"\1":"******"', log_text)
    pattern_url = r'([?&]token(?:=|%3D))[^&]*'
    masked = re.sub(pattern_url, r'\1******', masked)
    return masked

# ==================== 日志文件操作 ====================
LOG_DIR = "./log"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR, exist_ok=True)

def get_today_log_path():
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"{today}.log")

def write_request_log(log_data: dict):
    log_file = get_today_log_path()
    log_line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {json.dumps(log_data, ensure_ascii=False)}\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(log_line)

def read_today_logs():
    log_file = get_today_log_path()
    if not os.path.exists(log_file):
        return "暂无今日请求日志"
    with open(log_file, "r", encoding="utf-8") as f:
        return f.read()

def clear_today_log():
    log_file = get_today_log_path()
    if os.path.exists(log_file):
        open(log_file, "w", encoding="utf-8").close()
    return True

# ==================== HTML模板 ====================
LOG_HTML_TPL = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>API日志管理</title>
    <style>
        body{font-family:微软雅黑;padding:20px;background:#f5f7fa;}
        .box{background:#fff;padding:20px;border-radius:8px;box-shadow:0 1px 6px #ddd;}
        .btn{padding:8px 16px;background:#dc3545;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:14px;}
        .btn:hover{background:#bb2d3b;}
        pre{
            margin-top:15px;
            padding:15px;
            background:#ffffff;
            color:#222222;
            border:1px solid #ddd;
            font-size:12px;
            white-space:pre-wrap;
            word-break:break-all;
            max-height:70vh;
            overflow-y:auto;
        }
        .tip{color:#666;margin-bottom:10px;}
    </style>
</head>
<body>
    <div class="box">
        <h2>今日接口请求日志管理</h2>
        <p class="tip">当前日期：{{today}}</p>
        <button class="btn" onclick="clearLog()">清空今日日志</button>
        <pre>{{logs}}</pre>
    </div>
    <script>
        function clearLog(){
            if(confirm("确定清空今日全部日志？不可恢复！")){
                fetch("/clear_log?secret={{secret}}").then(res=>res.json()).then(ret=>{
                    if(ret.code===200){
                        alert("清空成功，页面刷新");
                        location.reload();
                    }else{
                        alert("清空失败："+ret.msg);
                    }
                })
            }
        }
    </script>
</body>
</html>
"""

SECRET_INPUT_TPL = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>访问日志需要密钥</title>
<style>
.box{width:400px;margin:100px auto;padding:30px;border:1px #ddd solid;border-radius:8px;}
input{width:100%;padding:8px;margin:10px 0;box-sizing:border-box;}
button{padding:8px 20px;background:#007bff;color:white;border:none;border-radius:4px;}
</style>
</head>
<body>
<div class="box">
    <h3>请输入日志访问密钥</h3>
    <input type="password" id="sec" placeholder="输入secret">
    <button onclick="go()">进入日志</button>
    <script>
        function go(){
            let s = document.getElementById('sec').value;
            location.href = "/logmanage?secret="+s;
        }
    </script>
</div>
</body>
</html>
"""