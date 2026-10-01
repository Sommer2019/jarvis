"""Dateien: PC ↔ Jarvis ↔ Handy, Ende-zu-Ende über HTTP."""

import importlib
import io
import json
import threading
import time
import urllib.request

import pytest

from jarvis import pc
from jarvis.files import FileStore, clean_name
from jarvis.pcstore import PcStore
from jarvis.phone import PhoneStore
from tests.test_pc import live  # noqa: F401  (Fixture: Server + MCP-Modul)


@pytest.fixture
def laptop(live, tmp_path, monkeypatch):
    port, data, mcp_pc = live
    home = tmp_path / "laptop"
    (home / "Dokumente").mkdir(parents=True)
    (home / "Dokumente" / "Steuererklaerung_2025.pdf").write_bytes(b"%PDF-1.4 Steuer")
    (home / "Dokumente" / "notizen.txt").write_text("Einkaufen: Milch, Brot", encoding="utf-8")
    (tmp_path / "geheim.txt").write_text("nicht freigegeben")
    monkeypatch.setattr(pc, "pc_config", lambda: {"roots": [str(home)]})
    monkeypatch.setattr(pc, "SAVE_DIR", home / "Downloads" / "Jarvis")
    agent = pc.Agent(f"http://127.0.0.1:{port}", "geheim", "laptop")
    threading.Thread(target=agent.run_forever, daemon=True).start()
    for _ in range(50):
        if PcStore(data).any_registered():
            break
        time.sleep(0.1)
    monkeypatch.setenv("JARVIS_WORKSPACE", str(data))  # Server-Fixture nutzt data als Workspace
    from jarvis import mcp_phone
    phone_mod = importlib.reload(mcp_phone)
    yield port, data, home, mcp_pc, phone_mod


def test_search_read_fetch_and_send(laptop):
    port, data, home, mcp_pc, mcp_phone = laptop
    hits = mcp_pc.pc_files_search("steuer")["result"]["results"]
    assert [h["name"] for h in hits] == ["Steuererklaerung_2025.pdf"]
    assert mcp_pc.pc_file_read(str(home / "Dokumente/notizen.txt"))["result"]["content"] == "Einkaufen: Milch, Brot"
    listing = mcp_pc.pc_files_list(str(home / "Dokumente"), "*.pdf")["result"]
    assert listing["count"] == 1

    got = mcp_pc.pc_fetch_file(hits[0]["path"])["result"]
    stored = data / got["workspace_path"]
    assert stored.read_bytes() == b"%PDF-1.4 Steuer" and got["source"].startswith("PC laptop")

    PhoneStore(data).register_app({"version": "1"})
    sent = mcp_phone.phone_send_file(got["id"], via="app")
    (act,) = PhoneStore(data).pending()
    assert act["type"] == "file" and act["params"]["name"] == "Steuererklaerung_2025.pdf" and sent["app"] == act["id"]

    # Die App lädt die Datei mit Token herunter
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/files/{got['id']}", headers={"Authorization": "Bearer geheim"})
    assert urllib.request.urlopen(req).read() == b"%PDF-1.4 Steuer"
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/files/{got['id']}")  # ohne Token


def test_phone_share_to_laptop(laptop):
    port, data, home, mcp_pc, mcp_phone = laptop
    meta = FileStore(data, data).save(io.BytesIO(b"\x89PNG foto"), "Urlaub.png", "Handy (geteilt)")
    assert mcp_phone.jarvis_files()[0]["name"] == "Urlaub.png"
    r1 = mcp_pc.pc_save_file(meta["id"])["result"]
    r2 = mcp_pc.pc_save_file(meta["id"])["result"]  # zweites Mal → kein Überschreiben
    assert r1["saved"].endswith("Downloads/Jarvis/Urlaub.png") and r2["saved"].endswith("Urlaub_1.png")
    assert (home / "Downloads/Jarvis/Urlaub.png").read_bytes() == b"\x89PNG foto"


def test_sandbox(laptop):
    port, data, home, mcp_pc, _ = laptop
    for bad in (str(home.parent / "geheim.txt"), str(home / ".." / "geheim.txt"), "/etc/passwd"):
        with pytest.raises(RuntimeError, match="außerhalb der freigegebenen Ordner"):
            mcp_pc.pc_file_read(bad)
    with pytest.raises(RuntimeError, match="Keine Textdatei"):
        mcp_pc.pc_file_read(str(home / "Dokumente/Steuererklaerung_2025.pdf"))
    assert clean_name("../../etc/passwd") == "passwd" and clean_name('a<b>:"c".txt') == "a_b___c_.txt"


def test_upload_endpoint_limits(live):
    port, data, _ = live
    boundary = "xyz"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"../../böse.txt\"\r\n\r\nhallo\r\n"
            f"--{boundary}--\r\n").encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/files", data=body, method="POST", headers={
        "Authorization": "Bearer geheim", "Content-Type": f"multipart/form-data; boundary={boundary}"})
    meta = json.loads(urllib.request.urlopen(req).read())
    assert meta["name"] == "böse.txt" and (data / meta["workspace_path"]).read_text() == "hallo"


def test_write_edit_with_backup(laptop, monkeypatch):
    laptop = laptop[2]
    monkeypatch.setattr(pc, "BACKUP_DIR", laptop / ".jarvis-backup")
    ok, res = pc.run_action({"type": "file_write", "params": {"path": str(laptop / "Notizen" / "neu.md"), "content": "# Liste\n- Milch"}})
    assert ok, res
    target = laptop / "Notizen" / "neu.md"
    assert target.read_text() == "# Liste\n- Milch"
    ok, res = pc.run_action({"type": "file_write", "params": {"path": str(target), "content": "x"}})
    assert not ok and "gibt es schon" in res  # create überschreibt nie
    ok, res = pc.run_action({"type": "file_write", "params": {"path": str(target), "content": "- Brot", "mode": "append"}})
    assert ok and target.read_text() == "# Liste\n- Milch\n- Brot"
    ok, res = pc.run_action({"type": "file_edit", "params": {"path": str(target), "old": "Milch", "new": "Hafermilch"}})
    assert ok and "Hafermilch" in target.read_text()
    assert (laptop / ".jarvis-backup").exists() and "Milch\n- Brot" in open(res["backup"]).read()
    ok, res = pc.run_action({"type": "file_edit", "params": {"path": str(target), "old": "Käse", "new": "x"}})
    assert not ok and "kommt in der Datei nicht vor" in res
    ok, res = pc.run_action({"type": "file_write", "params": {"path": "/etc/jarvis.txt", "content": "x"}})
    assert not ok and "außerhalb" in res
    ok, res = pc.run_action({"type": "file_write", "params": {"path": str(laptop / "a.docx"), "content": "x"}})
    assert not ok and "nicht direkt bearbeiten" in res


def test_read_office(laptop):
    import zipfile

    laptop = laptop[2]
    docx = laptop / "Brief.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:t>Sehr geehrte Frau M&amp;ller,</w:t>'
                   '</w:r></w:p><w:p><w:r><w:t>Kündigung zum 31.12.</w:t></w:r></w:p></w:body></w:document>')
    ok, res = pc.run_action({"type": "file_read", "params": {"path": str(docx)}})
    assert ok, res
    assert res["content"] == "Sehr geehrte Frau M&ller,\nKündigung zum 31.12."
    pptx = laptop / "Talk.pptx"
    with zipfile.ZipFile(pptx, "w") as z:
        for i in (2, 1, 10):
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld><a:p><a:t>Folie {i}</a:t></a:p></p:sld>")
        z.writestr("ppt/slides/_rels/slide1.xml.rels", "<x/>")
    ok, res = pc.run_action({"type": "file_read", "params": {"path": str(pptx)}})
    assert ok and res["content"].index("Folie 1") < res["content"].index("Folie 2") < res["content"].index("Folie 10")
