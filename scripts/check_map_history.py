"""Verify daily operator history using an isolated database and real HTTP."""
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from check_auth import Client, free_port, wait_for_server
from check_clarification import stop_server

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as temp:
    db = Path(temp) / 'history.db'
    base = f'http://127.0.0.1:{free_port()}'
    env = dict(os.environ, DATABASE_PATH=str(db), P109_AUTH_DISABLED='0',
               P109_DEMO_MODE='0', P109_SIGNUP_INVITE='history-test', P109_SECURE_COOKIES='0')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
                               '--port', base.rsplit(':', 1)[1]], cwd=root, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        anonymous, citizen, operator = Client(), Client(), Client()
        wait_for_server(anonymous, base)
        path = base + '/api/workspace/map-history?day=2026-09-29'
        assert anonymous.request(path)[0] == 401
        for client, role in [(citizen, 'citizen'), (operator, 'operator')]:
            code, result, _ = client.request(base + '/api/auth/signup', 'POST', {
                'name': role, 'email': role + '@example.test', 'password': 'Map-History-Test-129!',
                'role': role, 'invite_code': 'history-test'})
            assert code == 201, result
        assert citizen.request(path)[0] == 403
        with sqlite3.connect(db) as conn:
            ids = [row[0] for row in conn.execute('SELECT id FROM complaints ORDER BY id LIMIT 7')]
            conn.execute("UPDATE complaints SET received_at='2026-10-01T00:00:00Z'")
            times = ['2026-09-28T18:59:59.999Z', '2026-09-28T19:00:00Z',
                     '2026-09-29T07:00:00Z', '2026-09-29T18:59:59.999Z',
                     '2026-09-29T19:00:00Z', '2026-09-29T08:00:00Z', '2026-09-29T09:00:00Z']
            for cid, at in zip(ids, times):
                conn.execute("UPDATE complaints SET data_origin='citizen', received_at=?, quarantined=0, latitude=NULL, longitude=NULL WHERE id=?", (at, cid))
            conn.execute('UPDATE complaints SET latitude=43.2461819, longitude=76.9269795 WHERE id=?', (ids[1],))
            conn.execute("UPDATE complaints SET data_origin='synthetic' WHERE id=?", (ids[5],))
            conn.execute('UPDATE complaints SET quarantined=1 WHERE id=?', (ids[6],))
        code, result, _ = operator.request(path)
        assert code == 200
        assert {item['id'] for item in result['items']} == set(ids[1:4])
        exact = next(item for item in result['items'] if item['id'] == ids[1])
        assert exact['latitude'] == 43.2461819 and exact['longitude'] == 76.9269795
        assert sum(item['latitude'] is None for item in result['items']) == 2
        assert operator.request(path + '&source=synthetic')[1]['count'] == 1
        assert operator.request(path + '&source=all')[1]['count'] == 4
        assert operator.request(base + '/api/workspace/map-history?day=invalid')[0] == 422
        assert operator.request(path + '&source=invalid')[0] == 422
        print('PASS: operator-only history; UTC+5 day boundaries; source separation; quarantine excluded; exact/missing coordinates; input validation')
    finally:
        stop_server(process)
