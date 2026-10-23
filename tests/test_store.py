"""Storage invariants (N2, N4, N5, N11): one cell per household, an append-only
tamper-evident ledger, crypto-shredding, dedupe, idempotency and optimistic concurrency.
"""
import json
import os
import time

import pytest

from careloop import store, timecraft
from careloop.config import Settings


def add(cell, quote, *, kind="medication", person="Papa", needs_confirm=False, **kw):
    sched = timecraft.parse_schedule(quote, anchor_date=time.strftime("%Y-%m-%d"))
    return cell.add_item(person=person, kind=kind, sched_text=quote[:60], sched_json=sched,
                         quote=quote, qstart=0, qend=len(quote), transcript_hash="th-" + person,
                         source="rules", confidence=0.9, needs_confirm=needs_confirm, **kw)


# ------------------------------------------------------------------ cell isolation
def test_two_households_get_two_database_files(cell, settings):
    g = store.global_db()
    other = g.create_household("OTHER1", "111111")
    a, b = cell, store.open_cell(other)
    try:
        assert a.db_path != b.db_path
        assert os.path.dirname(a.db_path) != os.path.dirname(b.db_path)
        add(a, "Take 500 mg paracetamol")
        assert b.list_items() == []
    finally:
        b.close()


def test_a_cell_never_opens_another_household_file(cell, settings):
    g = store.global_db()
    other = g.create_household("OTHER2", "111111")
    other_cell = store.open_cell(other)
    try:
        add(other_cell, "Walk twenty minutes after dinner")
        assert cell.get_item(other_cell.list_items()[0]["ref"]) is None
    finally:
        other_cell.close()


# ------------------------------------------------------------------ extractive-by-construction
def test_the_displayed_text_is_exactly_the_recorded_quote(cell):
    quote = "Take the white tablet with milk, not water"
    item, status = add(cell, quote)
    assert status == "added"
    assert item["text"] == quote
    assert item["quote"] == quote


def test_item_refs_are_stable_ids(cell):
    item, _ = add(cell, "Check the sugar on Monday morning")
    assert item["ref"].startswith("i-")
    assert cell.get_item(item["ref"])["ref"] == item["ref"]


# ------------------------------------------------------------------ dedupe
def test_repeating_the_same_instruction_does_not_duplicate_it(cell):
    first, status1 = add(cell, "Take 500 mg paracetamol twice a day")
    second, status2 = add(cell, "take 500 mg paracetamol, twice a day!")   # same normalised text
    assert status1 == "added" and status2 == "deduped"
    assert first["ref"] == second["ref"]
    assert len(cell.list_items()) == 1


def test_dedupe_never_resurrects_a_done_item(cell):
    item, _ = add(cell, "Take 500 mg paracetamol at bedtime")
    cell.mark_done(item["ref"])
    again, status = add(cell, "Take 500 mg paracetamol at bedtime")
    assert status == "deduped"
    assert again["status"] == "done"
    assert len(cell.list_items(statuses=("pending",))) == 0


def test_different_people_are_not_deduped_together(cell):
    add(cell, "Take 500 mg paracetamol", person="Papa")
    _, status = add(cell, "Take 500 mg paracetamol", person="Mama")
    assert status == "added"


# ------------------------------------------------------------------ idempotency
def test_add_item_with_an_idempotency_key_replays_the_first_response(cell):
    q = "Do the breathing exercises after breakfast"
    item, status = add(cell, q, idem_key="k-1")
    assert status == "added"
    replay, status2 = add(cell, q + " again later in the day", idem_key="k-1")
    assert status2 == "replayed"
    assert replay["ref"] == item["ref"]


def test_mark_done_is_idempotent_per_key(cell):
    item, _ = add(cell, "Take 500 mg paracetamol in the morning")
    r1 = cell.mark_done(item["ref"], who="Mama", idem_key="d-1")
    r2 = cell.mark_done(item["ref"], who="Mama", idem_key="d-1")
    assert r1["status"] == "done"
    assert r2.get("replayed") is True
    assert cell.get_item(item["ref"])["version"] == 2      # version moved exactly once


def test_mark_done_on_an_unknown_ref_is_a_plain_not_found(cell):
    assert cell.mark_done("i-deadbeef") == {"error": "not-found"}


# ------------------------------------------------------------------ optimistic concurrency
def test_a_stale_version_is_rejected_and_reports_the_current_one(cell):
    item, _ = add(cell, "Take 500 mg paracetamol at night")
    assert cell.mark_done(item["ref"], version=item["version"])["status"] == "done"
    res = cell.mark_done(item["ref"], version=item["version"])
    assert res["error"] == "version-conflict"
    assert res["current_version"] == item["version"] + 1


def test_the_version_increases_on_every_mutation(cell):
    item, _ = add(cell, "Stop the water tablets for now", needs_confirm=True)
    v0 = item["version"]
    cell.confirm_item(item["ref"])
    assert cell.get_item(item["ref"])["version"] == v0 + 1
    cell.mark_done(item["ref"])
    assert cell.get_item(item["ref"])["version"] == v0 + 2


# ------------------------------------------------------------------ confirmation + delete
def test_flagged_items_stay_flagged_until_confirmed(cell):
    item, _ = add(cell, "Increase the insulin to four units", needs_confirm=True)
    assert item["needs_confirm"] is True
    assert cell.confirm_item(item["ref"]) == {"ref": item["ref"], "needs_confirm": False}
    assert cell.get_item(item["ref"])["needs_confirm"] is False


def test_delete_requires_an_explicit_confirmation_round_trip(cell):
    item, _ = add(cell, "Take 500 mg paracetamol")
    first = cell.delete_item(item["ref"])
    assert first["needs_confirmation"] is True
    assert first["summary"]["quote"] == "Take 500 mg paracetamol"
    assert cell.get_item(item["ref"]) is not None          # nothing was destroyed yet
    assert cell.delete_item(item["ref"], confirm=True)["deleted"] is True
    assert cell.get_item(item["ref"]) is None


def test_deleting_something_absent_is_reported_as_not_found(cell):
    assert cell.delete_item("i-00000000", confirm=True) == {"error": "not-found"}


def test_superseded_items_leave_the_active_plan(cell):
    old, _ = add(cell, "Take 500 mg paracetamol twice a day")
    new, _ = add(cell, "Take 500 mg paracetamol three times a day")
    cell.supersede(old["ref"], new["ref"])
    assert cell.get_item(old["ref"])["status"] == "superseded"
    assert [i["ref"] for i in cell.list_items(statuses=("pending",))] == [new["ref"]]


# ------------------------------------------------------------------ ledger (N4)
def test_the_ledger_starts_empty_then_chains(cell):
    assert cell.verify_ledger() == {"ok": True, "events": 0, "head": "GENESIS"}
    add(cell, "Take 500 mg paracetamol")
    add(cell, "Walk after dinner", kind="task", person="Mama")
    st = cell.verify_ledger()
    assert st["ok"] and st["events"] == 2 and st["head"] != "GENESIS"


def test_each_event_carries_the_previous_hash(cell):
    add(cell, "Take 500 mg paracetamol")
    add(cell, "Stop the salt", kind="avoid")
    rows = cell.conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
    assert rows[0]["prev_hash"] == "GENESIS"
    assert rows[1]["prev_hash"] == rows[0]["hash"]
    assert rows[1]["seq"] == rows[0]["seq"] + 1


def test_editing_one_event_breaks_the_chain(cell):
    add(cell, "Take 500 mg paracetamol")
    add(cell, "Walk after dinner", kind="task")
    cell.conn.execute("UPDATE events SET payload=? WHERE seq=1",
                      (json.dumps({"ref": "i-forged", "person": "Papa", "kind": "medication"}),))
    cell.conn.commit()
    st = cell.verify_ledger()
    assert st["ok"] is False
    assert st["broken_at"] == 1
    assert "hash" in st["reason"]


def test_removing_one_event_is_detected_as_a_gap(cell):
    add(cell, "Take 500 mg paracetamol")
    add(cell, "Walk after dinner", kind="task")
    add(cell, "Rest the knee", kind="task", person="Mama")
    cell.conn.execute("DELETE FROM events WHERE seq=1")
    cell.conn.commit()
    st = cell.verify_ledger()
    assert st["ok"] is False and "gap" in st["reason"]


def test_every_mutation_kind_is_audited(cell):
    item, _ = add(cell, "Take 500 mg paracetamol")
    cell.mark_done(item["ref"])
    cell.confirm_item(item["ref"])
    cell.delete_item(item["ref"], confirm=True)
    types = [r["type"] for r in cell.conn.execute("SELECT type FROM events ORDER BY seq")]
    assert types == ["item_added", "item_status", "item_confirmed", "item_deleted"]


def test_the_ledger_replays_to_a_time_travel_snapshot(cell):
    """N5: 'what was the plan on Tuesday?' is answered from the ledger, not a shadow table."""
    before = time.time()
    add(cell, "Take 500 mg paracetamol")
    item, _ = add(cell, "Walk after dinner", kind="task")
    cell.mark_done(item["ref"])
    mid = time.time()
    add(cell, "Stop the salt", kind="avoid")
    now = time.time()
    assert cell.rebuild_state(until_ts=before) == {}
    snap = cell.rebuild_state(until_ts=mid)
    assert len(snap) == 2
    assert snap[item["ref"]]["status"] == "done"          # the mark_done event is replayed
    assert len(cell.rebuild_state(until_ts=now)) == 3
    assert len(cell.rebuild_state()) == 3


def test_verify_all_cells_covers_every_household(cell):
    add(cell, "Take 500 mg paracetamol")
    cell.close()
    report = store.verify_all_cells()
    assert report and all(v.get("ok") for v in report.values()), report


# ------------------------------------------------------------------ shredding (N11)
def test_shred_destroys_the_cell_and_its_key_and_leaves_a_receipt(cell, settings):
    cell.close()
    c = store.open_cell(cell.hid, encrypt=True)     # only an encrypted cell has a key to shred
    add(c, "Take 500 mg paracetamol twice a day")
    db_path, key_path, directory = c.db_path, c.key_path, c.dir
    assert os.path.exists(db_path) and os.path.exists(key_path)
    res = c.shred()
    assert res["erased"] is True
    assert res["receipt"].startswith("er-")
    assert "KMS" in res["note"]                       # honest about the production path
    assert not os.path.exists(db_path) and not os.path.exists(key_path)
    assert not os.path.exists(directory)


def test_an_erasure_receipt_is_kept_without_any_personal_data(cell):
    add(cell, "Take 500 mg paracetamol")
    res = cell.shred()
    rows = store.tombstones()
    assert [r["receipt"] for r in rows] == [res["receipt"]]
    assert len(rows[0]["hid_hash"]) == 32
    assert "paracetamol" not in json.dumps(rows)      # the receipt holds no PHI


def test_a_shredded_household_cannot_be_read_back(cell, settings):
    add(cell, "Take 500 mg paracetamol")
    hid = cell.hid
    cell.shred()
    fresh = store.open_cell(hid, encrypt=False)       # a new empty cell, not the old data
    try:
        assert fresh.list_items() == []
        assert fresh.verify_ledger()["events"] == 0
    finally:
        fresh.close()


def test_a_busy_cell_refuses_to_claim_it_was_erased(cell):
    """A receipt is a claim about the disk. If the file is still open elsewhere we must say so."""
    add(cell, "Take 500 mg paracetamol")
    other = store.open_cell(cell.hid, encrypt=False)      # a second live connection
    try:
        res = other.shred()
        assert res["erased"] is False
        assert res["left_behind"] and "receipt" not in res
        assert "No erasure receipt" in res["note"]
    finally:
        cell.close()
    assert not store.tombstones()


# ------------------------------------------------------------------ visits, no plaintext transcript
def test_only_the_transcript_hash_is_stored(cell):
    transcript = "Doctor: your haemoglobin is low. Please take 200 mg iron after dinner every day."
    vid, th = cell.record_visit("Papa", transcript, "rules", None, 0.0)
    assert th == store.sha256_hex(transcript)
    visits = cell.visits()
    assert [v["id"] for v in visits] == [vid]
    assert transcript not in json.dumps(visits, default=str)
    assert "haemoglobin" not in json.dumps(visits, default=str)


def test_a_visit_can_be_proved_without_revealing_it(cell):
    vid, th = cell.record_visit("Papa", "Doctor said to rest the knee", "rules", None, 0.0)
    g = store.global_db()
    assert g.verify_quote_hash(cell.hid, th)
    assert not g.verify_quote_hash(cell.hid, "0" * 64)


@pytest.mark.skipif(not store.HAVE_CRYPTO, reason="cryptography is not installed")
def test_field_encryption_keeps_plaintext_out_of_the_database_file(cell, settings):
    c = store.open_cell(cell.hid, encrypt=True)
    try:
        quote = "Take 500 mg paracetamol for the knee pain"
        add(c, quote)
        raw = open(c.db_path, "rb").read()
        assert quote.encode() not in raw
        assert c.get_item(c.list_items()[0]["ref"])["quote"] == quote   # but still readable
    finally:
        c.close()


@pytest.mark.skipif(not store.HAVE_CRYPTO, reason="cryptography is not installed")
def test_the_ledger_payload_is_encrypted_yet_the_chain_still_verifies(cell, settings):
    """A care event quotes a clinician, so the payload must not sit in the file in plain
    text either. The hash still covers the plaintext, because that is the content we have
    to be able to prove — which is exactly why the two cannot be traded against each other."""
    c = store.open_cell(cell.hid, encrypt=True)
    try:
        quote = "Take 20 mg rivaroxaban once daily with the evening meal"
        add(c, quote)
        raw = open(c.db_path, "rb").read()
        assert quote.encode() not in raw
        blobs = [r["payload"] for r in c.conn.execute("SELECT payload FROM events ORDER BY seq")]
        assert blobs and all(b.startswith("enc:") for b in blobs), blobs
        assert c.verify_ledger()["ok"] is True
        row = c.conn.execute("SELECT * FROM events WHERE type='item_added'").fetchone()
        assert json.loads(c.event_body(row))["quote"] == quote
    finally:
        c.close()


@pytest.mark.skipif(not store.HAVE_CRYPTO, reason="cryptography is not installed")
def test_a_plaintext_payload_written_into_an_encrypted_ledger_is_detected(cell, settings):
    """Tampering with the audit trail by overwriting the column is the obvious attack, so
    the check must not be 'does it decrypt' but 'does it still hash to what we signed'."""
    c = store.open_cell(cell.hid, encrypt=True)
    add(c, "Take 20 mg rivaroxaban once daily with the evening meal")
    hid = c.hid
    c.close()
    forged = store.open_cell(hid, encrypt=True)
    try:
        forged.conn.execute("UPDATE events SET payload=? WHERE seq=1",
                            ('{"quote": "Take nothing at all"}',))
        forged.conn.commit()
        st = forged.verify_ledger()
        assert st["ok"] is False, st
        assert st["broken_at"] == 1, st
        assert "hash" in st["reason"] or "unreadable" in st["reason"], st
    finally:
        forged.close()


@pytest.mark.skipif(not store.HAVE_CRYPTO, reason="cryptography is not installed")
def test_without_the_key_the_ledger_fails_loud_instead_of_looking_clean(cell, settings):
    """The crypto-shredding promise in one test: destroy the key and the history is not
    merely unreadable, it is provably unverifiable — an auditor is never shown a green tick
    over data nobody can read."""
    c = store.open_cell(cell.hid, encrypt=True)
    add(c, "Take 20 mg rivaroxaban once daily with the evening meal")
    add(c, "Check the blood pressure every morning")
    hid, key_path = c.hid, c.key_path
    c.close()
    with open(key_path, "wb") as f:
        f.write(os.urandom(32))                       # the old key is gone; the file is not
    fresh = store.open_cell(hid, encrypt=True)
    try:
        st = fresh.verify_ledger()
        assert st["ok"] is False, st
        assert "unreadable" in st["reason"], st
        assert fresh.event_body(fresh.conn.execute("SELECT * FROM events LIMIT 1").fetchone()) is None
        report = store.verify_all_cells()
        bad = [v for v in report.values() if not v.get("ok")]
        assert bad and all("unreadable" in (v.get("reason") or "") for v in bad), report
    finally:
        fresh.close()


# ------------------------------------------------------------------ people + context
def test_person_aliases_resolve_to_one_canonical_name(cell):
    cell.add_person("Ramesh", "elder", aliases=["papa", "father"])
    assert cell.resolve_person("Papa") == "Ramesh"
    assert cell.resolve_person("  father ") == "Ramesh"
    assert cell.resolve_person("uncle") is None
    assert cell.person_role("Ramesh") == "elder"
    assert cell.person_role("nobody") == "viewer"


def test_add_person_merges_aliases_instead_of_duplicating(cell):
    cell.add_person("Suresh", "elder", ["mother"])
    cell.add_person("Suresh", "elder", ["aunty"])
    people = cell.people()
    assert len(people) == 1
    assert people[0]["aliases"] == ["aunty", "mother"]


def test_cross_device_context_round_trip(cell):
    item, _ = add(cell, "Take 500 mg paracetamol")
    cell.set_context("Papa", [item["ref"]], ttl=300)
    got = cell.get_context(ttl=300)
    assert got == {"person": "Papa", "refs": [item["ref"]]}
    cell.clear_context()
    assert cell.get_context(ttl=300) is None


def test_context_expires_rather_than_answering_about_a_stale_screen(cell):
    item, _ = add(cell, "Take 500 mg paracetamol")
    cell.set_context("Papa", [item["ref"]], ttl=300)
    cell.conn.execute("UPDATE ctx SET updated_at=?", (time.time() - 3600,))
    cell.conn.commit()
    assert cell.get_context(ttl=300) == {"expired": True}
    assert cell.get_context(ttl=300) is None          # the expired row was deleted


# ------------------------------------------------------------------ diff + adherence
def test_what_changed_separates_new_from_stopped(cell):
    old, _ = add(cell, "Take 500 mg paracetamol twice a day")
    time.sleep(0.01)
    boundary = time.time()
    new, _ = add(cell, "Take 500 mg paracetamol three times a day")
    cell.supersede(old["ref"], new["ref"])
    diff = cell.what_changed(boundary)
    assert [i["ref"] for i in diff["new"]] == [new["ref"]]
    assert [i["ref"] for i in diff["stopped"]] == [old["ref"]]


def test_adherence_counts_today_against_the_rest(cell):
    today = time.strftime("%Y-%m-%d")
    a, _ = add(cell, "Take 500 mg paracetamol in the morning")
    b, _ = add(cell, "Walk after dinner", kind="task")
    cell.mark_done(a["ref"])
    assert cell.adherence(today) == {"done_today": 1, "pending": 1, "history": 1}
    assert cell.adherence("2020-01-01")["done_today"] == 0


# ------------------------------------------------------------------ global registry
def test_household_codes_join_rather_than_hijack(settings):
    """A second 'create' with a stolen code must land in the same cell, not replace its PIN."""
    g = store.global_db()
    hid = g.create_household("AAA111", "481542")
    assert g.create_household("AAA111", "999999") == hid
    assert g.check_pin(hid, "481542") and not g.check_pin(hid, "999999")
    row = g.conn.execute("SELECT pin_hash FROM households WHERE hid=?", (hid,)).fetchone()
    assert "481542" not in row["pin_hash"]
    assert g.household_by_code("aaa111")["hid"] == hid
    with pytest.raises(ValueError):
        g.create_household("!!", "1234")


def test_unlinking_kills_the_tokens_of_that_link_only(settings):
    g = store.global_db()
    hid = g.create_household("BBB222", "123456")
    keep = g.add_link(hid, "caregiver-one", "caregiver")
    drop = g.add_link(hid, "caregiver-two", "viewer")
    for link in (keep, drop):
        g.conn.execute("INSERT INTO tokens(token_hash, kind, hid, sub, link_id, scope, client_id,"
                       " audience, expires_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (f"{link}h", "access", hid, link, link, "mcp:tools", "c", "aud",
                        time.time() + 100, time.time()))
    g.conn.commit()
    g.unlink(drop)
    assert [l["link_id"] for l in g.links(hid)] == [keep]
    assert g.link(keep)["status"] == "linked" and g.link(drop)["status"] == "unlinked"
    assert g.conn.execute("SELECT revoked FROM tokens WHERE link_id=?", (drop,)).fetchone()["revoked"] == 1
    assert g.conn.execute("SELECT revoked FROM tokens WHERE link_id=?", (keep,)).fetchone()["revoked"] == 0


# ------------------------------------------------------------------ chaos: db_locked (N19)
def test_db_locked_leaves_every_read_working(cell, settings):
    cell.add_person("Papa")
    a, _ = add(cell, "Take 500 mg paracetamol in the morning")
    settings.chaos = {"db_locked"}
    assert cell.get_item(a["ref"])["ref"] == a["ref"]
    assert [i["ref"] for i in cell.list_items()] == [a["ref"]]
    assert cell.verify_ledger()["ok"] is True
    assert [p["name"] for p in cell.people()] == ["Papa"] and cell.visits() == []
    assert cell.adherence()["pending"] == 1


@pytest.mark.parametrize("call", [
    lambda c: c.add_person("Papa"),
    lambda c: add(c, "Take 500 mg paracetamol"),
    lambda c: c.mark_done("i-00000000"),
    lambda c: c.confirm_item("i-00000000"),
    lambda c: c.supersede("i-00000000", "i-00000001"),
    lambda c: c.delete_item("i-00000000", confirm=True),
    lambda c: c.flag("i-00000000"),
    lambda c: c.record_visit("Papa", "Rest the knee", "rules", None, 0.0),
    lambda c: c.shred(),
], ids=["add-person", "add-item", "mark-done", "confirm", "supersede", "delete",
        "flag", "record-visit", "shred"])
def test_db_locked_refuses_every_write(cell, settings, call):
    settings.chaos = {"db_locked"}
    with pytest.raises(store.StoreLocked):
        call(cell)


def test_a_refused_write_leaves_the_plan_and_ledger_untouched(cell, settings):
    a, _ = add(cell, "Take 500 mg paracetamol in the morning")
    before = cell.verify_ledger()
    settings.chaos = {"db_locked"}
    with pytest.raises(store.StoreLocked):
        cell.mark_done(a["ref"])
    settings.chaos = set()
    assert cell.get_item(a["ref"])["status"] == "pending"      # healed, not half-done
    assert cell.verify_ledger() == before


def test_db_locked_does_not_break_the_disposable_context(cell, settings):
    """Context is conversational scratch, not care data: losing it must not lose the answer."""
    a, _ = add(cell, "Take 500 mg paracetamol in the morning")
    settings.chaos = {"db_locked"}
    cell.set_context("Papa", [a["ref"]], ttl=300)
    cell.clear_context()
    assert cell.get_context(300) is None


def test_db_locked_refuses_to_erase_without_a_receipt(cell, settings):
    add(cell, "Take 500 mg paracetamol in the morning")
    settings.chaos = {"db_locked"}
    with pytest.raises(store.StoreLocked):
        cell.shred()
    settings.chaos = set()
    assert os.path.exists(cell.db_path)
    assert cell.get_item(cell.list_items()[0]["ref"])["status"] == "pending"


def test_the_chaos_flag_names_are_the_ones_the_code_injects():
    from careloop.config import CHAOS_FLAGS
    assert set(CHAOS_FLAGS) == {"bedrock_throttle", "verify_down", "latency_800ms", "db_locked"}
    s = Settings(env={"CARELOOP_CHAOS": "db_locked,comprehend_down"})
    assert s.chaos == {"db_locked", "comprehend_down"}
    assert s.chaos_unknown == ["comprehend_down"]
