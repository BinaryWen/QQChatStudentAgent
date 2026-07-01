# sql_mapping.py
from datetime import date, timedelta

def get_next_7_dates() -> list[str]:
    today = date.today()
    date_list = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    return date_list

# 新增：获取近3天日期字符串（用于聊天表send_time匹配日期）
def get_recent_3_dates() -> list[str]:
    today = date.today()
    date_list = []
    for i in range(0, 3):
        d = today - timedelta(days=i)
        date_list.append(f"{d.month}月{d.day}日")
    return date_list

def build_weekly_event_sql() -> str:
    today = date.today()
    date_list = []
    for i in range(7):
        d = today + timedelta(days=i)
        date_list.append(f"{d.month}日")
    conds = [f"content_text LIKE '%{d}%'" for d in date_list]
    where = " OR ".join(conds)
    sql = f"""
    SELECT send_time,sender_name,content_text,file_name,file_url 
    FROM qq_chat_records 
    WHERE {where} 
    ORDER BY send_time DESC LIMIT 100
    """
    return sql.strip()

# 新增：近3天聊天记录SQL构造函数
def build_recent3msg_sql() -> str:
    date_arr = get_recent_3_dates()
    conds = [f"content_text LIKE '%{d}%'" for d in date_arr]
    where_clause = " OR ".join(conds)
    sql = f"""
    SELECT send_time, group_name, sender_name, content_text, file_name, file_url
    FROM qq_chat_records
    WHERE {where_clause}
    ORDER BY send_time DESC LIMIT 200
    """
    return sql.strip()

def build_weekly_exam_sql() -> str:
    date_arr = get_next_7_dates()
    date_str = ",".join([f"'{d}'" for d in date_arr])
    sql = f"""
    SELECT id, classroom, count, course, date, period, time, room, campus, main, invi
    FROM examination_arrange 
    WHERE date IN ({date_str}) 
    ORDER BY date ASC, period ASC
    """
    return sql.strip()

def build_checkforclass_sql(class_param: str) -> str:
    safe_val = class_param.replace("'", "''")
    sql = f"""
    SELECT id, classroom, count, course, date, period, time, room, campus, main, invi
    FROM examination_arrange
    WHERE classroom LIKE '%{safe_val}%'
    ORDER BY date ASC, period ASC
    LIMIT 100
    """
    return sql.strip()

BASE_QUERY_MAPPING = {
    "user_info": "SELECT DISTINCT sender_name, group_name FROM qq_chat_records LIMIT 100",
    "group_list": "SELECT DISTINCT group_name FROM qq_chat_records",
    "class_list": "SELECT DISTINCT classroom FROM examination_arrange",
    "recent_msg": "SELECT * FROM qq_chat_records WHERE send_time >= NOW() - INTERVAL 3 DAY ORDER BY send_time DESC LIMIT 30",
    "recent3_events": build_recent3msg_sql(),  # 新增3天聊天查询
    "msg_count": "SELECT group_name, COUNT(*) as msg_num FROM qq_chat_records GROUP BY group_name",
    "calc_25_789_review": "SELECT send_time,sender_name,content_text,file_name,file_url FROM qq_chat_records WHERE group_name='华工微积分25级工科试验789班' AND (content_text LIKE '%微积分复习%' OR content_text LIKE '%复习%' OR content_text LIKE '%框架%') ORDER BY send_time DESC LIMIT 50",
    "weekly_exam": build_weekly_exam_sql()
}

# 关键：这个函数必须存在，解决ImportError
def get_full_query_mapping():
    mapping = BASE_QUERY_MAPPING.copy()
    mapping["weekly_events"] = build_weekly_event_sql()
    return mapping

# 接口统一获取SQL方法，处理checkforclass动态参数
def get_final_sql(xqm: str, class_arg: str = None) -> str:
    full_map = get_full_query_mapping()
    if xqm == "checkforclass":
        if not class_arg or class_arg.strip() == "":
            raise ValueError("XQM=checkforclass 必须传入 class 参数")
        return build_checkforclass_sql(class_arg.strip())
    if xqm in full_map:
        return full_map[xqm]
    support_keys = ",".join(full_map.keys())
    raise ValueError(f"XQM仅支持：{support_keys}")