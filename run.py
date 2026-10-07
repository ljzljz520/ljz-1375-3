#!/usr/bin/env python3
"""启动沙漠驿站资料馆。

用法: python3 run.py [db_path] [port] [基准日]
默认: data/archive.db, 8080, 2026-10-07
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import db
from app.web import make_server

def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/archive.db"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    day = sys.argv[3] if len(sys.argv) > 3 else "2026-10-07"
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    fresh = db_path == ":memory:" or not os.path.exists(db_path)
    conn = db.connect(db_path)
    db.init(conn)
    if fresh:
        db.seed(conn, day)
        print("已初始化种子数据。")
    httpd = make_server(conn, "0.0.0.0", port, day)
    print(f"沙漠驿站资料馆运行于 http://127.0.0.1:{port}/ （基准日 {day}）")
    print("首次使用请到 /editor：先“运行带证据关系推断”，逐条批准后执行“后台发布”。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
