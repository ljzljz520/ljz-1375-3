"""数据库层：SQLite schema 与种子数据。

建模要点：
- sources：来源（历史档案 / 口述 / 当代调查 / 照片及许可）。
- entities：历史地理实体（水井 / 商队 / 民居 / 工艺）。
- service_points：当前服务实体（补给点）。二者独立，不共享 id，不靠名称关联。
- statements：关于实体/服务点的陈述，带出处、类型、版本链。
  kind = evidence(历史证据) | oral(口述推测) | tip(当代提示) | photo(照片)
  value_type = coordinate | fact | tip | name
- relations：关系（连接实体之间或历史实体与服务点），
  status = candidate(带证据推断) | approved(人工确认) | rejected。
- itineraries：已审行程（含有效窗口）。
- published_docs / published_cards：服务端实际发布记录（文章快照 / 打印卡快照）。
- publish_tasks：后台发布任务（重建受影响文章，任务不复活已撤内容）。
- bundles：离线包（有效期）。
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  ref TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,            -- archive|oral|survey|photo
  author TEXT,
  date TEXT,
  url TEXT,
  license_status TEXT NOT NULL DEFAULT 'valid', -- valid|expired
  license_expires TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS entities (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,            -- well(水井)|caravan(商队)|dwelling(民居)|craft(工艺)
  name TEXT NOT NULL,
  era TEXT,
  summary TEXT,
  dirty INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS service_points (
  id TEXT PRIMARY KEY,          -- 当前服务实体，与历史实体完全独立
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'supply',
  lat REAL,
  lng REAL,
  coord_certain INTEGER NOT NULL DEFAULT 1, -- 1 确定坐标; 0 不确定
  status TEXT NOT NULL DEFAULT 'open',      -- open|closed
  closed_effective TEXT,
  services TEXT,                -- JSON 数组，如 ["water","fuel","food"]
  notes TEXT,
  dirty INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS statements (
  id INTEGER PRIMARY KEY,
  target_table TEXT NOT NULL,   -- entity|service
  target_id TEXT NOT NULL,
  kind TEXT NOT NULL,           -- evidence|oral|tip|photo
  value_type TEXT NOT NULL,     -- coordinate|fact|tip|name|photo
  claim TEXT NOT NULL,          -- 陈述文本
  value TEXT,                   -- 归一化值（坐标为 "lat,lng"; fact 为归一化键值 JSON）
  source_id INTEGER REFERENCES sources(id),
  version INTEGER NOT NULL DEFAULT 1,
  supersedes INTEGER,           -- 被修订的旧陈述 id
  status TEXT NOT NULL DEFAULT 'active', -- active|superseded|withdrawn
  valid_from TEXT,
  valid_to TEXT,
  reviewer TEXT,
  created TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS relations (
  id INTEGER PRIMARY KEY,
  a_table TEXT NOT NULL, a_id TEXT NOT NULL,
  b_table TEXT NOT NULL, b_id TEXT NOT NULL,
  rtype TEXT NOT NULL,          -- same_name(同名)|uses(使用)|made_by(工艺)...
  provenance TEXT NOT NULL,     -- inferred(带证据推断)|manual(完全人工确认)
  status TEXT NOT NULL DEFAULT 'candidate', -- candidate|approved|rejected
  evidence_statement_ids TEXT,  -- JSON 数组，推断依据的陈述
  reviewer TEXT,
  reviewed TEXT,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_rel_a ON relations(a_table, a_id, status);
CREATE INDEX IF NOT EXISTS idx_rel_b ON relations(b_table, b_id, status);

CREATE TABLE IF NOT EXISTS itineraries (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'reviewed', -- reviewed|draft
  valid_from TEXT NOT NULL,
  valid_to TEXT NOT NULL,
  stops TEXT NOT NULL            -- JSON: [{"table":"service","id":"s1"}, ...]
);

CREATE TABLE IF NOT EXISTS published_docs (
  doc_key TEXT PRIMARY KEY,     -- entity:<id> 或 service:<id>
  target_table TEXT NOT NULL,
  target_id TEXT NOT NULL,
  title TEXT NOT NULL,
  html TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS published_cards (
  card_key TEXT PRIMARY KEY,    -- card:<id>
  target_table TEXT NOT NULL,
  target_id TEXT NOT NULL,
  html TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publish_tasks (
  id INTEGER PRIMARY KEY,
  target_table TEXT NOT NULL,
  target_id TEXT NOT NULL,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'pending', -- pending|done
  created TEXT NOT NULL,
  finished TEXT
);

CREATE TABLE IF NOT EXISTS bundles (
  id INTEGER PRIMARY KEY,
  doc_keys TEXT NOT NULL,        -- JSON 数组
  built_at TEXT NOT NULL,
  valid_to TEXT NOT NULL,
  payload TEXT
);
"""

# ---------- 种子数据 ----------
SEED_SOURCES = [
    # ref, title, kind, author, date, url, license_status, license_expires, notes
    ("S-QING-1882", "敦煌县府西路水程册（光绪八年抄本）", "archive", "敦煌县府", "1882",
     None, "valid", None, "记载驿站、井名与相距里数"),
    ("S-HEDIN-1901", "斯文·赫定西域考察日记", "archive", "Sven Hedin", "1901",
     None, "valid", None, "罗布之路考察记录"),
    ("S-ORAL-01", "买买提·阿卜拉口述：沙泉子井", "oral", "买买提·阿卜拉", "2019-05-12",
     None, "valid", None, "由乡文化站录音整理，定位为凭记忆估指"),
    ("S-ORAL-02", "热依汗口述：红柳井旧地", "oral", "热依汗", "2021-09-03",
     None, "valid", None, "记忆位置，与档案里数不一致"),
    ("S-SURVEY-2023", "当代戈壁补给点踏勘报告", "survey", "区域交通调查队", "2023-06",
     None, "valid", None, "含 GPS 与开闭调信息"),
    ("S-SURVEY-2024", "井址考订简报（第二期）", "survey", "文保所", "2024-04",
     None, "valid", None, "修订沙泉子井定位"),
    ("S-PHOTO-A7", "沙泉子旧井照片 A7", "photo", "佚名向导", "1998",
     None, "valid", "2026-06-30", "授权用于资料馆，已到期（用于验收）"),
    ("S-PHOTO-B2", "红柳井夯土台照片 B2", "photo", "考察队", "2020",
     None, "valid", "2030-12-31", "授权有效"),
]

SEED_ENTITIES = [
    # id, kind, name, era, summary
    ("w1", "well", "沙泉子井", "清代", "西路水程册所载驿站水井，沙泉子驿站得名于此。"),
    ("w2", "well", "红柳井", "清代", "红柳井旧地，口述与档案里数存在分歧。"),
    ("c1", "caravan", "阿不旦商队", "清末", "往返阿不旦与敦煌的驼队，惯用沙泉子井补水。"),
    ("d1", "dwelling", "沙泉子驿站民居", "清代", "驿站旁夯土民居一组。"),
    ("k1", "craft", "红柳井夯土工艺", "清代", "盐碱土夹红柳分层夯筑工艺。"),
]

SEED_SERVICES = [
    # id, name, lat, lng, coord_certain, status, closed_effective, services, notes
    ("s1", "沙泉子补给点", 40.0550, 94.3200, 1, "open", None,
     '["water","fuel","food"]', "当代公路补给点，沿用旧名，但与古井并非同一实体。"),
    ("s2", "红柳井服务区", 40.1800, 94.0100, 1, "closed", "2026-02-01",
     '["water"]', "因水源改造关闭，2026-02-01 起停止服务。"),
    ("s3", "无名野驼点", None, None, 0, "open", None,
     '["water"]', "有补水传闻，位置未经核实，坐标不确定。"),
]

# 陈述： (target_table,target_id,kind,value_type,claim,value,source_ref,valid_from,valid_to,note)
SEED_STATEMENTS = [
    ("entity", "w1", "evidence", "coordinate",
     "光绪水程册按里数推算井在驿站西北约三里", "40.0610,94.3140", "S-QING-1882",
     None, None, "古里换算，约 1.5km 误差"),
    ("entity", "w1", "oral", "coordinate",
     "老人指认旧井在今补给点西侧沙梁后", "40.0548,94.3130", "S-ORAL-01",
     None, None, "记忆估指，不确定"),
    ("entity", "w1", "evidence", "fact",
     "2024 考订：档案里数折算后井址应在旧道岔口",
     '{"coord":"40.0595,94.3122"}', "S-SURVEY-2024",
     None, None, "井址考证修订（新证据版本）"),
    ("entity", "w1", "evidence", "photo",
     "旧井照片（1998）：井框与辘轳残迹", None, "S-PHOTO-A7",
     None, None, "照片许可 2026-06-30 到期"),
    ("entity", "w1", "tip", "tip",
     "补给点旁有新修水龙头，水质检测合格（旧井咸苦不可直饮）",
     '{"window":"2025-01-01..2026-12-31","service":"water"}', "S-SURVEY-2023",
     "2025-01-01", "2026-12-31", "当代提示，不回溯为历史事实"),
    ("entity", "w2", "evidence", "coordinate",
     "水程册记红柳井在红柳台西五里", "40.1850,93.9980", "S-QING-1882",
     None, None, ""),
    ("entity", "w2", "oral", "coordinate",
     "热依汗指认在土公路转弯处", "40.1920,94.0060", "S-ORAL-02",
     None, None, "与档案冲突，待考"),
    ("entity", "w2", "evidence", "photo",
     "夯土台远景照片", None, "S-PHOTO-B2", None, None, "许可有效"),
    ("entity", "w2", "tip", "tip",
     "邻近红柳井服务区可补水（注意其已关闭）",
     '{"window":"2025-01-01..2026-01-31","service":"water"}', "S-SURVEY-2023",
     "2025-01-01", "2026-01-31", "窗口已于 2026-01-31 结束"),
    ("entity", "c1", "evidence", "fact",
     "赫定日记载阿不旦驼队在沙泉子井歇脚补水",
     '{"uses":"w1"}', "S-HEDIN-1901", None, None, ""),
    ("entity", "d1", "evidence", "fact",
     "民居夯层中含红柳枝，与红柳井工艺一致",
     '{"made_by":"k1"}', "S-QING-1882", None, None, ""),
    ("service", "s1", "evidence", "fact",
     "补给点 2023 踏勘在营，GPS 定位", '{"coord":"40.0550,94.3200"}',
     "S-SURVEY-2023", None, None, ""),
    ("service", "s2", "evidence", "fact",
     "服务区 2026-02-01 起因改造关闭", '{"closed":"2026-02-01"}',
     "S-SURVEY-2023", None, None, "关闭证据"),
    ("service", "s3", "oral", "tip",
     "货车司机说野驼点有时能补水，位置说不准",
     '{"window":"2025-03-01..2026-10-01","service":"water"}', "S-ORAL-01",
     "2025-03-01", "2026-10-01", "坐标不确定，仅提示"),
]

SEED_ITINERARIES = [
    # id, name, status, valid_from, valid_to, stops(JSON)
    ("i1", "敦煌—阿不旦西路（已审线）", "reviewed", "2026-01-01", "2026-12-31",
     '[{"table":"service","id":"s1"},{"table":"service","id":"s2"}]'),
]

def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()

def seed(conn, now="2026-10-07"):
    cur = conn.cursor()
    src_ref_to_id = {}
    for s in SEED_SOURCES:
        cur.execute(
            "INSERT INTO sources(ref,title,kind,author,date,url,license_status,license_expires,notes)"
            " VALUES (?,?,?,?,?,?,?,?,?)", s)
        src_ref_to_id[s[0]] = cur.lastrowid
    for e in SEED_ENTITIES:
        cur.execute("INSERT INTO entities(id,kind,name,era,summary,dirty) VALUES (?,?,?,?,?,1)", e)
    for sp in SEED_SERVICES:
        cur.execute(
            "INSERT INTO service_points(id,name,lat,lng,coord_certain,status,closed_effective,"
            "services,notes,dirty) VALUES (?,?,?,?,?,?,?,?,?,1)", sp)
    for st in SEED_STATEMENTS:
        tt, tid, kind, vt, claim, value, sref, vf, vt2, note = st
        cur.execute(
            "INSERT INTO statements(target_table,target_id,kind,value_type,claim,value,source_id,"
            "version,status,valid_from,valid_to,created,note)"
            " VALUES (?,?,?,?,?,?,?,1,'active',?,?,?,?)",
            (tt, tid, kind, vt, claim, value, src_ref_to_id[sref], vf, vt2, now, note))
    for it in SEED_ITINERARIES:
        cur.execute(
            "INSERT INTO itineraries(id,name,status,valid_from,valid_to,stops) VALUES (?,?,?,?,?,?)",
            it)
    conn.commit()
    return src_ref_to_id
