"""Run account isolation and migration checks against a temporary local database."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from check_auth import Client, free_port, wait_for_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from auth import init_auth


def run():
    with tempfile.TemporaryDirectory() as temp:
        db = Path(temp) / 'accounts.db'
        # Reproduce the previous schema with an existing account and session.
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE complaints (id TEXT PRIMARY KEY);
                CREATE TABLE auth_users (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    full_name TEXT NOT NULL, password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'operator')),
                    created_at INTEGER NOT NULL, disabled INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO auth_users VALUES ('old', 'old@example.kz', 'Old', 'hash', 'operator', 1, 0);
            """)
            conn.executescript("""CREATE TABLE auth_sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER,
                FOREIGN KEY (user_id) REFERENCES auth_users(id));
                INSERT INTO auth_sessions VALUES ('token', 'old', 'csrf', 1, 9999999999, NULL);
            """)
            init_auth(conn)
            init_auth(conn)
            assert conn.execute("SELECT role FROM auth_users WHERE id='old'").fetchone()[0] == 'operator'
            assert conn.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0] == 1
            assert not list(conn.execute('PRAGMA foreign_key_check'))
        print('PASS: legacy migration is repeatable; accounts and sessions preserved')
        db = Path(temp) / 'fresh.db'
        port = free_port()
        base = f'http://127.0.0.1:{port}'
        env = dict(os.environ, DATABASE_PATH=str(db), P109_SIGNUP_INVITE='test-invite', P109_AUTH_DISABLED='0', P109_DEMO_MODE='0')
        with open(Path(temp) / 'server.log', 'w') as log:
            server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                anonymous, alice, bob, operator = Client(), Client(), Client(), Client()
                wait_for_server(anonymous, base)
                password = 'account-check-password'
                def signup(client, name, **extra):
                    return client.request(base+'/api/auth/signup', 'POST', dict(name=name, email=name+'@example.kz', password=password, **extra))
                assert signup(anonymous, 'intruder', role='operator')[0] == 403
                assert signup(anonymous, 'admin', role='admin')[0] == 422
                status, account, _ = signup(alice, 'alice')
                assert status == 201 and account['user']['role'] == 'citizen'
                assert signup(bob, 'bob')[0] == 201
                status, account, _ = signup(operator, 'operator', role='operator', invite_code='test-invite')
                assert status == 201 and account['user']['role'] == 'admin'
                print('PASS: public signup cannot acquire operator/admin rights, including first account')
                for path in ['/api/workspace/queue', '/api/stats', '/api/workspace/operators', '/api/reports?format=pdf']:
                    assert alice.request(base+path)[0] == 403, path
                assert alice.request(base+'/api/workspace/seed', 'POST', {})[0] == 403
                assert operator.request(base+'/api/workspace/queue')[0] == 200
                headers = {'X-CSRF-Token': alice.cookie('pulse109_csrf')}
                payload = dict(text='Проверка кабинета: нет воды в доме', region_id='KZ-ALA', language='ru', owner_user_id='spoofed')
                ids = []
                for path in ['/api/intake', '/api/workspace/intake']:
                    assert alice.request(base+path, 'POST', payload)[0] == 403
                    status, result, _ = alice.request(base+path, 'POST', payload, headers)
                    assert status == 201, result
                    ids.append(result['id'])
                print('PASS: role boundaries and CSRF cover both intake routes')
                own = alice.request(base+'/api/workspace/citizen/complaints')[1]['items']
                assert {item['id'] for item in own} == set(ids)
                assert bob.request(base+'/api/workspace/citizen/complaints')[1]['items'] == []
                assert anonymous.request(base+'/api/workspace/citizen/complaints')[0] == 401
                for cid in ids:
                    path = base+'/api/workspace/tracking/'+cid
                    status, result, response_headers = alice.request(path)
                    assert status == 200 and result['owned'] and result['data_origin'] != 'synthetic'
                    assert response_headers['Cache-Control'] == 'no-store'
                    assert bob.request(path)[0] == anonymous.request(path)[0] == 404
                    assert alice.request(base+'/api/complaints/'+cid)[0] == 403
                cid = ids[-1]
                with sqlite3.connect(db) as conn:
                    conn.execute("INSERT INTO complaint_photos (complaint_id,mime_type,content,created_at) VALUES (?, 'image/png', ?, '2026-09-29')", (cid, b'test-image'))
                path = base+f'/api/workspace/citizen/complaints/{cid}/photo'
                assert alice.request(path)[0] == 200
                assert bob.request(path)[0] == 404
                assert anonymous.request(path)[0] == 401
                print('PASS: lists, history and private attachments are isolated between citizens')
                op_headers = {'X-CSRF-Token': operator.cookie('pulse109_csrf')}
                assert operator.request(base+f'/api/workspace/complaints/{cid}/reply', 'POST', {'text':'Проверяем проблему'}, op_headers)[0] == 200
                history = alice.request(base+'/api/workspace/tracking/'+cid)[1]
                assert any(item['text']=='Проверяем проблему' for item in history['updates'])
                assert alice.request(base+'/api/auth/logout', 'POST', {}, headers)[0] == 200
                assert alice.request(base+'/api/workspace/tracking/'+cid)[0] == 404
                assert alice.request(base+'/api/auth/login', 'POST', {'email':'alice@example.kz','password':password})[0] == 200
                assert len(alice.request(base+'/api/workspace/citizen/complaints')[1]['items']) == 2
                print('PASS: operator replies reach owner; logout revokes access; login restores saved cases')
                print('ALL CITIZEN CHECKS PASSED')
            finally:
                server.terminate()
                server.wait(timeout=5)


if __name__ == '__main__':
    run()
