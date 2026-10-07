"""Seed a demonstration database.

All dates are anchored at 2026-10-07 so the acceptance scenarios can be
exercised deterministically.
"""
from __future__ import annotations

from . import core
from .db import connect, init_db

ANCHOR = "2026-10-07"


def seed(conn):
    core.TODAY_OVERRIDE = ANCHOR

    # ------------------------------------------------------------ sources
    qing_ce = core.create_source(
        conn, "historical", "敦煌县西路道里册（抄本）", "卷三·驿站",
        "1892-06-01", notes="清末驿站里程与井泉记录")
    oral_luo = core.create_source(
        conn, "oral", "驼工罗占魁口述", "1987 年访谈第 12 号录音带",
        "1987-09-14", notes="记忆性材料，定位与可用性均为推测")
    survey = core.create_source(
        conn, "contemporary", "县交通局公路补给点普查表", "表号 JT-2026-红沙",
        "2026-03-20", notes="当代服务设施登记")
    gazetteer = core.create_source(
        conn, "historical", "肃州方志·水利志", "1936 年铅印本", "1936-01-01")
    oral_ma = core.create_source(
        conn, "oral", "向导马德福口述", "2001 年访谈记录", "2001-05-02")
    craft_doc = core.create_source(
        conn, "historical", "河陇手工业调查稿", "手稿残页", "1951-08-09")

    # ------------------------------------------------------------ entities
    well_old = core.create_entity(
        conn, "well", "红沙井", era="清末—民国",
        summary="驿站使用的历史井址；与当代补给点名称相同，但属独立历史地理实体。")
    supply_new = core.create_entity(
        conn, "well", "红沙井", era="当代",
        summary="2021 年设立的公路补给点；名称沿用旧井名，不保证旧井仍可使用。")
    west_old = core.create_entity(
        conn, "well", "西腰站井", era="民国", summary="已干涸的旧井，旁有废弃补给站。")
    caravan = core.create_entity(
        conn, "caravan", "罗记驼队", era="民国", summary="往返肃州—哈密的商队。")
    dwelling = core.create_entity(
        conn, "dwelling", "驿站东院民居", era="清末", summary="驿站守卒及商户居所。")
    craft_rope = core.create_entity(
        conn, "craft", "井绳鞣制", era="清末—当代", summary="井绳鞣制与修理工艺。")
    craft_leather = core.create_entity(
        conn, "craft", "皮囊修补", era="民国", summary="驮水皮囊缝补工艺。")

    # -------------------------------------------------- statements + relations
    # historical well: evidence-backed attributes
    st_route = core.create_statement(
        conn, well_old["id"], qing_ce["id"], "located_along",
        value="肃州至哈密西路第三台", note="道里册明确记载", category="evidence")
    core.set_statement_confidence(conn, st_route["id"], "approved")

    st_era2 = core.create_statement(
        conn, gazetteer["id"] and well_old["id"], gazetteer["id"], "dug_in",
        value="清道光年间", note="方志记为道光七年开凿", category="evidence") if False else None
    st_dug = core.create_statement(
        conn, well_old["id"], gazetteer["id"], "dug_in",
        value="清道光七年", note="方志记为道光七年开凿", category="evidence")
    core.set_statement_confidence(conn, st_dug["id"], "approved")

    # source CONFLICT: oral account says 光绪 — kept open for editorial ruling
    st_dug_conflict = core.create_statement(
        conn, well_old["id"], oral_ma["id"], "dug_in",
        value="光绪年间", note="口述记忆，与方志冲突，待考", category="speculation")

    # oral speculation: the well MIGHT coincide with modern point (never auto-linked)
    st_oral_link = core.create_statement(
        conn, well_old["id"], oral_luo["id"], "same_site_as",
        value="老人说井口就在今天的红沙井补给站院里", value_ref=supply_new["id"],
        category="speculation", note="口述推测：名称相同且位置可能重合，尚无考古证据")

    # modern supply point facts
    st_supply = core.create_statement(
        conn, supply_new["id"], survey["id"], "facility",
        value="2021 年设公路补给点，售水、简餐、应急油料",
        category="tip", note="当代提示")
    core.set_statement_confidence(conn, st_supply["id"], "approved")
    st_supply_serves = core.create_statement(
        conn, supply_new["id"], survey["id"], "serves_site",
        value="补给点为红沙井旧井址一带的旅人提供服务", value_ref=well_old["id"],
        category="evidence", note="经普查人员核对的独立服务关联（非因同名）")
    core.set_statement_confidence(conn, st_supply_serves["id"], "approved")

    # approved, manually confirmed relation: modern service entity -> historical site
    rel_serves = core.create_relation(
        conn, supply_new["id"], well_old["id"], "serves", st_supply_serves["id"])
    core.approve_relation(conn, rel_serves["id"])

    # a second name-based proposal remains UNAPPROVED (shown only in console)
    core.create_relation(conn, well_old["id"], supply_new["id"], "serves",
                         st_oral_link["id"]) if False else None
    rel_name = core.create_relation(
        conn, well_old["id"], supply_new["id"], "serves", st_oral_link["id"])
    # deliberately NOT approved: same name is not proof

    # caravan / dwelling / craft web (evidence + one oral speculation)
    st_use = core.create_statement(
        conn, caravan["id"], qing_ce["id"], "uses",
        value="往返西路第三台取水", value_ref=well_old["id"], category="evidence")
    core.set_statement_confidence(conn, st_use["id"], "approved")
    rel_use = core.create_relation(conn, caravan["id"], well_old["id"], "uses_well", st_use["id"])
    core.approve_relation(conn, rel_use["id"])

    st_lodge = core.create_statement(
        conn, caravan["id"], oral_luo["id"], "lodges",
        value="驼队常在驿站东院歇脚", value_ref=dwelling["id"], category="speculation")
    core.set_statement_confidence(conn, st_lodge["id"], "approved")
    rel_lodge = core.create_relation(conn, caravan["id"], dwelling["id"], "lodges_at", st_lodge["id"])
    core.approve_relation(conn, rel_lodge["id"])

    st_craft = core.create_statement(
        conn, craft_rope["id"], craft_doc["id"], "practiced_at",
        value="井绳鞣制在东院进行", value_ref=dwelling["id"], category="evidence")
    core.set_statement_confidence(conn, st_craft["id"], "approved")
    rel_craft = core.create_relation(conn, craft_rope["id"], dwelling["id"],
                                     "practiced_at", st_craft["id"])
    core.approve_relation(conn, rel_craft["id"])

    st_draw = core.create_statement(
        conn, craft_leather["id"], oral_ma["id"], "draws_material",
        value="皮囊补匠与鞣绳匠共用东院工棚", value_ref=craft_rope["id"],
        category="speculation")
    # remains proposed: unconfirmed link is invisible on the website

    # ------------------------------------------------------------ locations
    # exact pin for the modern point (approved survey)
    loc_exact = core.add_location(
        conn, supply_new["id"], st_supply["id"], "exact", 40.3510, 95.1204)
    # historical well: oral guess is only approximate → no precise pin
    st_oral_pos = core.create_statement(
        conn, well_old["id"], oral_luo["id"], "at_position",
        value="旧井大概在红沙梁南坡", category="speculation",
        note="口述推测坐标，未获批准，不得补成精确图钉")
    core.add_location(conn, well_old["id"], st_oral_pos["id"],
                      "approximate", 40.348, 95.126)
    # west well: no credible location at all
    st_west = core.create_statement(
        conn, west_old["id"], gazetteer["id"], "condition",
        value="民国末已干涸", category="evidence", note="方志记载")
    core.set_statement_confidence(conn, st_west["id"], "approved")
    st_west_pos = core.create_statement(
        conn, west_old["id"], oral_ma["id"], "at_position",
        value="位置无人能确认", category="speculation")
    core.add_location(conn, west_old["id"], st_west_pos["id"], "unlocated")

    # ------------------------------------------------------------ photos
    photo_ok = core.add_photo(
        conn, well_old["id"], "东院残存井台（2019 年摄）", "cc-by-sa", "2028-12-31")
    photo_lapsed = core.add_photo(
        conn, supply_new["id"], "补给点门头旧照", "permission", "2026-09-01")
    # license already lapsed relative to ANCHOR → must not publish
    core.refresh_photo_status(conn)

    # ------------------------------------------------------------ supply
    sp_open = core.upsert_supply_point(
        conn, supply_new["id"], "open",
        services=["饮用水", "简餐", "应急油料"], note="公路服务区运营中")
    core.add_service_window(conn, supply_new["id"], "2026-01-01", "2026-12-31", "全年营业")
    sp_west = core.upsert_supply_point(
        conn, west_old["id"], "closed", services=[],
        note="废弃补给站，2025 年起关闭，无水无油")
    core.add_service_window(conn, west_old["id"], "2020-01-01", "2024-12-31", "已终止")

    # ------------------------------------------------------------ itineraries
    itin = core.create_itinerary(
        conn, "红沙线三日行", "2026-10-01", "2026-10-20",
        note="已审标准行程", reviewed=True)
    core.add_segment(conn, itin["id"], 1, "出发：红沙井当代补给点",
                     supply_new["id"], "start", "确认开放并在窗口内")
    core.add_segment(conn, itin["id"], 2, "古道探访：红沙井旧井址",
                     well_old["id"], "water_well",
                     "历史井址；需经已批准的 serves 关系才能视为可补给")
    core.add_segment(conn, itin["id"], 3, "绕行点：西腰站井",
                     west_old["id"], "water_well", "已关闭，预计产生缺口提示")

    itin2 = core.create_itinerary(
        conn, "未审候选线（草稿）", "2026-10-01", "2026-11-30",
        note="编辑尚未审核，不能用于旅行资料检查", reviewed=False)
    core.add_segment(conn, itin2["id"], 1, "西腰站井", west_old["id"], "water_well")

    # ------------------------------------------------------------ first publish
    core.enqueue_publish(conn, [e["id"] for e in core.list_entities(conn)], "初始发布")
    core.run_pending_tasks(conn)
    core.build_package(conn, ttl_days=30)

    return {
        "sources": {"qing_ce": qing_ce["id"], "oral_luo": oral_luo["id"],
                    "survey": survey["id"], "gazetteer": gazetteer["id"],
                    "oral_ma": oral_ma["id"]},
        "entities": {"well_old": well_old["id"], "supply_new": supply_new["id"],
                     "west_old": west_old["id"], "caravan": caravan["id"],
                     "dwelling": dwelling["id"], "craft_rope": craft_rope["id"],
                     "craft_leather": craft_leather["id"]},
        "statements": {
            "oral_link": st_oral_link["id"], "supply_serves": st_supply_serves["id"],
            "dug": st_dug["id"], "dug_conflict": st_dug_conflict["id"],
            "oral_pos": st_oral_pos["id"], "west_pos": st_west_pos["id"],
            "leather_draw": st_draw["id"]},
        "relations": {"serves": rel_serves["id"], "name_proposal": rel_name["id"]},
        "locations": {"exact": loc_exact["id"]},
        "photos": {"ok": photo_ok["id"], "lapsed": photo_lapsed["id"]},
        "itineraries": {"reviewed": itin["id"], "draft": itin2["id"]},
    }


def main(db_path="data/archive.db", reset=False):
    from pathlib import Path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    if reset:
        Path(db_path).unlink(missing_ok=True)
    conn = connect(db_path)
    init_db(conn)
    ids = seed(conn)
    conn.close()
    return ids


if __name__ == "__main__":
    import json
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/archive.db"
    print(json.dumps(main(db_path, reset=True), ensure_ascii=False, indent=2))
