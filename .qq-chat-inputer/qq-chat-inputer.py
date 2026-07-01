import json
import os
import shutil
import logging
import pymysql
from pymysql.err import OperationalError, ProgrammingError
from pathlib import Path
from datetime import datetime, timedelta

# ===================== 基础路径配置（全绝对路径） =====================
# 脚本所在根目录（日志文件生成在此目录下）
SCRIPT_ROOT = os.path.dirname(os.path.abspath(__file__))
import os
from dotenv import load_dotenv  # 可选：若使用.env文件
load_dotenv()  # 可选：加载.env文件
CONFIG = {
    "mysql": {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DB")
    },
    # 待处理JSON和resources的根目录（绝对路径）
    "base_dir": "/root/.qq-chat-exporter/scheduled-exports",
    # 归档目标根目录（绝对路径）
    "history_dir": "/root/.qq-chat-exporter/history",
    # 数据表名
    "table_name": "qq_chat_records"
}

# Debug与功能开关
ARCHIEVE_OFF = False  # True=不归档文件，False=启用归档
LIST_DROP = False     # True=启动前询问是否删表，False=不询问
DEBUG_LOG = False     # True=开启详细调试日志（写入日志文件）
ENABLE_DEDUPLICATION = True  # True=开启重复消息去重，False=关闭

# ===================== 日志配置 =====================
def init_logger():
    """初始化日志：debug信息写入脚本根目录的日志文件，控制台仅输出核心信息"""
    logger = logging.getLogger("qq_chat_importer")
    logger.setLevel(logging.DEBUG if DEBUG_LOG else logging.INFO)
    logger.handlers.clear()  # 避免重复添加handler

    # 文件处理器：写入所有debug和info日志
    log_file_path = os.path.join(SCRIPT_ROOT, "qq_chat_import.log")
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # 控制台处理器：仅输出INFO及以上级别（核心进度）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    return logger

# 全局日志实例
logger = init_logger()

# ===================== 核心处理类 =====================
class QQChatImporter:
    def __init__(self):
        # 初始化数据库连接
        self.conn = self._get_mysql_conn()
        self.cursor = self.conn.cursor()
        # 确保目录存在（均为绝对路径）
        Path(CONFIG["history_dir"]).mkdir(parents=True, exist_ok=True)
        Path(CONFIG["base_dir"]).mkdir(parents=True, exist_ok=True)
        # 建表
        self._create_table_if_not_exists()
        logger.info("初始化完成，日志文件路径：" + os.path.join(SCRIPT_ROOT, "qq_chat_import.log"))

    def _get_mysql_conn(self):
        """创建MySQL连接"""
        try:
            conn = pymysql.connect(
                host=CONFIG["mysql"]["host"],
                user=CONFIG["mysql"]["user"],
                password=CONFIG["mysql"]["password"],
                database=CONFIG["mysql"]["database"],
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor
            )
            return conn
        except OperationalError as e:
            logger.error(f"MySQL连接失败: {e}")
            raise Exception(f"MySQL连接失败，请检查服务和账号密码: {e}")

    def _create_table_if_not_exists(self):
        """创建数据表（自增主键+标准化群名字段）"""
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS {CONFIG["table_name"]} (
            id INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
            group_name VARCHAR(128) COMMENT '标准化群聊名称',
            send_time DATETIME COMMENT '发送时间',
            sender_name VARCHAR(64) COMMENT '发送人名称',
            content_text TEXT COMMENT '消息文本内容',
            file_name VARCHAR(256) COMMENT '附件文件名',
            file_url VARCHAR(512) COMMENT '附件相对路径',
            INDEX idx_group_name (group_name),
            INDEX idx_send_time (send_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='QQ群聊记录表';
        """
        try:
            self.cursor.execute(create_sql)
            self.conn.commit()
            logger.debug("数据表检查/创建完成")
        except ProgrammingError as e:
            logger.error(f"创建表失败: {e}")
            raise Exception(f"创建表失败: {e}")

    def _drop_table_if_needed(self):
        """启动前询问是否删除原表"""
        if not LIST_DROP:
            return
        confirm = input(f"是否删除原有表 {CONFIG['table_name']}？(y/n): ")
        if confirm.strip().lower() == "y":
            drop_sql = f"DROP TABLE IF EXISTS {CONFIG['table_name']}"
            self.cursor.execute(drop_sql)
            self.conn.commit()
            logger.info("原表已删除，将重新创建数据表")
            self._create_table_if_not_exists()
        else:
            logger.info("保留原表，继续执行")

    def _standardize_group_name(self, raw_name):
        """
        标准化群聊名称：移除开头的 schedule- 前缀
        确保同一群聊的不同导出文件（带/不带前缀）归为同一群
        """
        if not raw_name:
            return "未知群聊"
        # 移除开头的 schedule- 前缀（仅移除第一个匹配项）
        if raw_name.startswith("schedule-"):
            standardized = raw_name[len("schedule-"):]
            logger.debug(f"【群名标准化】原始: {raw_name} → 标准化后: {standardized}")
            return standardized
        return raw_name

    def _check_file_exists(self, relative_url):
        """
        校验文件是否存在，返回 (是否存在, 绝对路径)
        全绝对路径拼接，无相对路径依赖
        """
        # 规范化相对路径，去除开头多余的斜杠
        relative_url = relative_url.lstrip("/\\")
        # 拼接绝对路径并规范化
        abs_path = os.path.abspath(os.path.join(CONFIG["base_dir"], relative_url))

        # 按要求格式写入debug日志
        logger.debug("【DEBUG】待检验文件路径（绝对）：" + abs_path)
        
        exists = os.path.isfile(abs_path)
        result_text = "存在" if exists else "不存在"
        logger.debug("【DEBUG】文件检验结果：" + result_text)

        return exists, abs_path

    def _archive_file(self, file_abs_path, relative_url):
        """归档文件到history目录，保留原目录结构"""
        if ARCHIEVE_OFF:
            return
        
        # 目标绝对路径
        relative_url = relative_url.lstrip("/\\")
        dst_abs = os.path.abspath(os.path.join(CONFIG["history_dir"], relative_url))
        # 创建目标目录
        Path(os.path.dirname(dst_abs)).mkdir(parents=True, exist_ok=True)
        
        try:
            shutil.move(file_abs_path, dst_abs)
            logger.debug(f"文件已归档至: {dst_abs}")
        except Exception as e:
            logger.error(f"文件归档失败 {file_abs_path}: {e}")

    def _archive_json_file(self, json_file_path):
        """归档处理完成的JSON文件"""
        if ARCHIEVE_OFF:
            return
        
        file_name = os.path.basename(json_file_path)
        dst_path = os.path.join(CONFIG["history_dir"], file_name)
        
        try:
            shutil.move(json_file_path, dst_path)
            logger.info(f"JSON文件已归档: {dst_path}")
        except Exception as e:
            logger.error(f"JSON文件归档失败 {json_file_path}: {e}")

    def _check_duplicate(self, record_data):
        """检查消息是否已存在（去重判断）"""
        if not ENABLE_DEDUPLICATION:
            return False
        
        check_sql = f"""
        SELECT COUNT(*) AS cnt FROM {CONFIG['table_name']}
        WHERE group_name = %s 
          AND send_time = %s 
          AND sender_name = %s 
          AND content_text = %s
        """
        try:
            self.cursor.execute(check_sql, (
                record_data["group_name"],
                record_data["send_time"],
                record_data["sender_name"],
                record_data["content_text"]
            ))
            result = self.cursor.fetchone()
            return result["cnt"] > 0
        except Exception as e:
            logger.warning(f"重复校验失败，默认不跳过: {e}")
            return False

    def update_log(self, total_message_count):
        """
        运维监控日志写入 history/log/update.txt
        运维判断规则：
        1. 第一行每日必更新 → 定时任务、入库脚本正常执行
        2. 第二行仅插入消息>0才更新日期 → 多日不变代表QQ导出无新数据/QQ进程掉线
        """
        try:
            # 计算昨日数据日期（凌晨执行的是前一天导出的JSON）
            today = datetime.now().date()
            data_date = today - timedelta(days=1)
            y, m, d = data_date.year, data_date.month, data_date.day

            log_path = Path(CONFIG["history_dir"]) / "log" / "update.txt"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            # 读取历史日志，保留第二行（最后有效数据日期）
            line1 = ""
            line2 = ""
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8") as f:
                    raw = [line.rstrip("\n") for line in f.readlines()]
                if len(raw) >= 1:
                    line1 = raw[0]
                if len(raw) >= 2:
                    line2 = raw[1]

            # 第一行强制更新：标识脚本今日正常运行，无论有无数据
            line1 = f"【脚本今日执行】{y}年{m}月{d}日，本次共入库{total_message_count}条聊天消息"
            # 第二行仅存在新增数据时更新，留存最后有效数据日期
            if total_message_count > 0:
                line2 = f"【最后有效QQ导出数据】{y}年{m}月{d}日，当日入库{total_message_count}条消息"

            # 覆盖写入，统一换行
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(line1 + "\n")
                f.write(line2 + "\n")

            # 赋予全局可读权限，方便公网接口读取
            os.chmod(log_path, 0o644)

        except Exception as err:
            logger.error(f"写入运维监控日志失败，不中断入库流程：{str(err)}")

    def _parse_single_message(self, group_name, message):
        """解析单条消息，返回入库数据字典"""
        # 基础字段提取
        send_time = message.get("time", "")
        sender_info = message.get("sender", {})
        sender_name = sender_info.get("name", sender_info.get("groupCard", "未知发送人"))
        content = message.get("content", {})
        content_text = content.get("text", "")
        
        # 文件相关字段初始化
        file_name = ""
        file_url = ""
        
        # 从content内部获取resources
        resources = content.get("resources", [])
        
        # 调试：打印资源情况
        if DEBUG_LOG and resources:
            logger.debug(f"【DEBUG】检测到消息包含资源，数量: {len(resources)}，类型列表: {[r.get('type') for r in resources]}")
        
        for res in resources:
            res_type = res.get("type", "").lower()
            if res_type == "file":
                # 命中file类型时打印调试日志
                logger.debug("【DEBUG】待检验文件类型：file")
                
                # 提取文件名和相对路径
                res_filename = res.get("filename", "")
                res_url = res.get("url", "")
                
                # 校验文件是否存在
                exists, abs_path = self._check_file_exists(res_url)
                if exists:
                    file_name = res_filename
                    file_url = res_url
                    # 归档文件
                    self._archive_file(abs_path, res_url)
                else:
                    logger.warning(f"文件不存在，跳过入库 | 相对路径: {res_url}")
                
                # 仅处理第一个file类型资源
                break
        
        return {
            "group_name": group_name,
            "send_time": send_time,
            "sender_name": sender_name,
            "content_text": content_text,
            "file_name": file_name,
            "file_url": file_url
        }

    def _insert_record(self, record_data):
        """插入单条记录到MySQL（含去重判断）"""
        # 去重校验
        if self._check_duplicate(record_data):
            logger.debug(f"重复消息，跳过插入 | 时间: {record_data['send_time']} | 发送人: {record_data['sender_name']}")
            return False
        
        insert_sql = f"""
        INSERT INTO {CONFIG["table_name"]} 
        (group_name, send_time, sender_name, content_text, file_name, file_url)
        VALUES (%s, %s, %s, %s, %s, %s);
        """
        try:
            self.cursor.execute(insert_sql, (
                record_data["group_name"],
                record_data["send_time"],
                record_data["sender_name"],
                record_data["content_text"],
                record_data["file_name"],
                record_data["file_url"]
            ))
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"数据插入失败: {e} | 发送时间: {record_data['send_time']}")
            return False

    def process_one_json(self, json_file_path):
        """处理单个JSON文件，返回成功插入的消息数量"""
        file_name = os.path.basename(json_file_path)
        logger.info(f"\n===== 开始处理文件: {file_name} =====")
        logger.debug(f"文件绝对路径: {json_file_path}")
        
        try:
            # 读取并解析JSON
            with open(json_file_path, "r", encoding="utf-8") as f:
                full_data = json.load(f)
            
            # 获取原始群名并标准化
            raw_group_name = full_data.get("chatInfo", {}).get("name", "未知群聊")
            standard_group_name = self._standardize_group_name(raw_group_name)
            logger.info(f"原始群名: {raw_group_name} | 标准化后群名: {standard_group_name}")
            
            # 遍历所有消息
            messages = full_data.get("messages", [])
            logger.info(f"消息总数: {len(messages)}")
            
            success_count = 0
            skip_count = 0
            file_count = 0
            for msg in messages:
                parsed = self._parse_single_message(standard_group_name, msg)
                insert_result = self._insert_record(parsed)
                if insert_result:
                    success_count += 1
                    if parsed["file_name"]:
                        file_count += 1
                else:
                    skip_count += 1
            
            logger.info(f"处理完成 | 成功入库: {success_count} 条 | 重复跳过: {skip_count} 条 | 包含有效文件: {file_count} 条")
            
            # 归档JSON文件
            self._archive_json_file(json_file_path)
            
            return success_count  # 返回成功插入的消息数量
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e} | 文件: {file_name}")
            return 0
        except Exception as e:
            logger.error(f"处理文件异常: {e} | 文件: {file_name}")
            import traceback
            logger.debug(traceback.format_exc())
            return 0

    def run(self):
        """主执行入口"""
        # 删表询问
        self._drop_table_if_needed()
        
        # 遍历目录下所有JSON文件
        base_dir = CONFIG["base_dir"]
        if not os.path.isdir(base_dir):
            logger.error(f"待处理目录不存在: {base_dir}")
            self.update_log(0)
            self._close_conn()
            return

        json_files = [f for f in os.listdir(base_dir) if f.endswith(".json")]
        
        # 无JSON文件的分支
        if not json_files:
            logger.info(f"目录 {base_dir} 下未找到JSON文件")
            self.update_log(0)
            logger.info("日志已更新，本次插入消息总数: 0")
            self._close_conn()
            logger.info("\n===== 所有文件处理完成 =====")
            return
        
        logger.info(f"找到 {len(json_files)} 个JSON文件待处理")
        
        total_inserted_messages = 0  # 汇总所有文件插入的消息总数
        
        for file in json_files:
            full_path = os.path.join(base_dir, file)
            inserted_count = self.process_one_json(full_path)
            total_inserted_messages += inserted_count
        
        # 更新日志，传入总插入消息数
        self.update_log(total_inserted_messages)
        logger.info(f"\n日志已更新，本次插入消息总数: {total_inserted_messages}")
        
        logger.info(f"\n所有JSON文件处理完成，共处理 {len(json_files)} 个文件，总计插入 {total_inserted_messages} 条消息")
        self._close_conn()
        logger.info("\n===== 所有文件处理完成 =====")

    def _close_conn(self):
        """统一关闭数据库连接"""
        try:
            self.cursor.close()
            self.conn.close()
            logger.info("数据库连接已关闭")
        except Exception as e:
            logger.error(f"关闭数据库连接异常: {e}")

# ===================== 运行入口 =====================
if __name__ == "__main__":
    try:
        importer = QQChatImporter()
        importer.run()
    except Exception as e:
        logger.error(f"程序执行失败: {e}")
        exit(1)
