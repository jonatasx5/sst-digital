"""
banco.py - Banco de dados com suporte a PostgreSQL e SQLite

Usa PostgreSQL quando DATABASE_URL está disponível (Railway)
Usa SQLite como fallback local
"""

import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Detecta se usa PostgreSQL ou SQLite
USE_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith("postgres"))

# Import lazy — não falha na inicialização se psycopg2 não estiver disponível
_psycopg2 = None
_psycopg2_extras = None

if USE_POSTGRES:
    try:
        import psycopg2 as _psycopg2
        import psycopg2.extras as _psycopg2_extras
        print("✅ psycopg2 carregado — usando PostgreSQL")
    except ImportError as e:
        print(f"⚠️  psycopg2 não disponível ({e}) — usando SQLite")
        USE_POSTGRES = False


def conectar():
    if USE_POSTGRES:
        conn = _psycopg2.connect(DATABASE_URL)
        return conn
    else:
        from config import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def executar(query, params=(), fetchone=False, fetchall=False, commit=False):
    """Executa query compatível com PostgreSQL e SQLite."""
    if USE_POSTGRES:
        query = query.replace("?", "%s")
        query = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        query = query.replace("datetime('now','localtime')", "NOW()")
        query = query.replace("OR IGNORE", "ON CONFLICT DO NOTHING")
        query = query.replace("INSERT OR IGNORE", "INSERT")

    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()

        cur.execute(query, params)
        result = None

        if fetchone:
            row = cur.fetchone()
            result = dict(row) if row else None
        elif fetchall:
            rows = cur.fetchall()
            result = [dict(r) for r in rows]

        if commit:
            conn.commit()

        if USE_POSTGRES and not fetchone and not fetchall:
            try:
                result = cur.fetchone()
                if result:
                    result = dict(result)
            except Exception:
                pass

        return result
    finally:
        conn.close()


def criar_banco():
    """Cria todas as tabelas."""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS funcionarios (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    cpf TEXT NOT NULL UNIQUE,
                    matricula TEXT,
                    cargo TEXT NOT NULL,
                    lotacao TEXT,
                    admissao TEXT,
                    celular TEXT,
                    email TEXT,
                    ativo INTEGER DEFAULT 1,
                    criado_em TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS funcao_documentos (
                    id SERIAL PRIMARY KEY,
                    cargo TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    UNIQUE(cargo, doc_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS lotes (
                    id SERIAL PRIMARY KEY,
                    descricao TEXT,
                    criado_em TIMESTAMP DEFAULT NOW(),
                    status TEXT DEFAULT 'pendente'
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS lote_funcionarios (
                    id SERIAL PRIMARY KEY,
                    lote_id INTEGER REFERENCES lotes(id),
                    funcionario_id INTEGER REFERENCES funcionarios(id),
                    status TEXT DEFAULT 'pendente',
                    UNIQUE(lote_id, funcionario_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS envios (
                    id SERIAL PRIMARY KEY,
                    lote_funcionario_id INTEGER REFERENCES lote_funcionarios(id),
                    funcionario_id INTEGER REFERENCES funcionarios(id),
                    doc_id TEXT NOT NULL,
                    doc_nome TEXT,
                    pdf_path TEXT,
                    autentique_id TEXT,
                    link_assinatura TEXT,
                    status TEXT DEFAULT 'pendente',
                    enviado_em TIMESTAMP,
                    assinado_em TIMESTAMP
                )
            """)
            conn.commit()
        else:
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS funcionarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
                cpf TEXT NOT NULL UNIQUE, matricula TEXT, cargo TEXT NOT NULL,
                lotacao TEXT, admissao TEXT, celular TEXT, email TEXT,
                ativo INTEGER DEFAULT 1, criado_em TEXT DEFAULT (datetime('now','localtime')))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS funcao_documentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, cargo TEXT NOT NULL,
                doc_id TEXT NOT NULL, UNIQUE(cargo, doc_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS lotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, descricao TEXT,
                criado_em TEXT DEFAULT (datetime('now','localtime')), status TEXT DEFAULT 'pendente')""")
            cur.execute("""CREATE TABLE IF NOT EXISTS lote_funcionarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, lote_id INTEGER, funcionario_id INTEGER,
                status TEXT DEFAULT 'pendente', UNIQUE(lote_id, funcionario_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS envios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, lote_funcionario_id INTEGER,
                funcionario_id INTEGER, doc_id TEXT NOT NULL, doc_nome TEXT,
                pdf_path TEXT, autentique_id TEXT, link_assinatura TEXT,
                status TEXT DEFAULT 'pendente', enviado_em TEXT, assinado_em TEXT)""")
            conn.commit()

        print(f"✅ Banco criado ({'PostgreSQL' if USE_POSTGRES else 'SQLite'})")
    finally:
        conn.close()


def buscar_funcionarios(termo="", apenas_ativos=True):
    conn = conectar()
    try:
        filtro = "AND ativo=1" if apenas_ativos else ""
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute(f"""SELECT * FROM funcionarios
                WHERE (nome ILIKE %s OR cpf ILIKE %s OR cargo ILIKE %s OR lotacao ILIKE %s) {filtro}
                ORDER BY nome""", (f"%{termo}%",)*4)
        else:
            cur = conn.cursor()
            cur.execute(f"""SELECT * FROM funcionarios
                WHERE (nome LIKE ? OR cpf LIKE ? OR cargo LIKE ? OR lotacao LIKE ?) {filtro}
                ORDER BY nome""", (f"%{termo}%",)*4)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def importar_funcionarios(lista):
    conn = conectar()
    inseridos = atualizados = 0
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()

        for f in lista:
            cpf = f.get("cpf", "").strip()
            if not cpf:
                continue

            if USE_POSTGRES:
                cur.execute("SELECT id FROM funcionarios WHERE cpf=%s", (cpf,))
            else:
                cur.execute("SELECT id FROM funcionarios WHERE cpf=?", (cpf,))

            existe = cur.fetchone()

            if existe:
                if USE_POSTGRES:
                    cur.execute("""UPDATE funcionarios SET nome=%s,cargo=%s,lotacao=%s,
                        admissao=%s,celular=%s,email=%s,ativo=1 WHERE cpf=%s""",
                        (f.get("nome",""),f.get("cargo",""),f.get("lotacao",""),
                         f.get("admissao",""),f.get("celular",""),f.get("email",""),cpf))
                else:
                    cur.execute("""UPDATE funcionarios SET nome=?,cargo=?,lotacao=?,
                        admissao=?,celular=?,email=?,ativo=1 WHERE cpf=?""",
                        (f.get("nome",""),f.get("cargo",""),f.get("lotacao",""),
                         f.get("admissao",""),f.get("celular",""),f.get("email",""),cpf))
                atualizados += 1
            else:
                if USE_POSTGRES:
                    cur.execute("""INSERT INTO funcionarios (nome,cpf,matricula,cargo,lotacao,admissao,celular,email)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (f.get("nome",""),cpf,f.get("matricula",""),f.get("cargo",""),
                         f.get("lotacao",""),f.get("admissao",""),f.get("celular",""),f.get("email","")))
                else:
                    cur.execute("""INSERT INTO funcionarios (nome,cpf,matricula,cargo,lotacao,admissao,celular,email)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (f.get("nome",""),cpf,f.get("matricula",""),f.get("cargo",""),
                         f.get("lotacao",""),f.get("admissao",""),f.get("celular",""),f.get("email","")))
                inseridos += 1

        conn.commit()
    finally:
        conn.close()
    return inseridos, atualizados


def salvar_funcionario(dados):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()

        fid = dados.get("id")
        if fid:
            if USE_POSTGRES:
                cur.execute("""UPDATE funcionarios SET nome=%s,cpf=%s,matricula=%s,cargo=%s,
                    lotacao=%s,admissao=%s,celular=%s,email=%s WHERE id=%s""",
                    (dados["nome"],dados["cpf"],dados.get("matricula",""),dados["cargo"],
                     dados.get("lotacao",""),dados.get("admissao",""),dados.get("celular",""),
                     dados.get("email",""),fid))
            else:
                cur.execute("""UPDATE funcionarios SET nome=?,cpf=?,matricula=?,cargo=?,
                    lotacao=?,admissao=?,celular=?,email=? WHERE id=?""",
                    (dados["nome"],dados["cpf"],dados.get("matricula",""),dados["cargo"],
                     dados.get("lotacao",""),dados.get("admissao",""),dados.get("celular",""),
                     dados.get("email",""),fid))
        else:
            if USE_POSTGRES:
                cur.execute("""INSERT INTO funcionarios (nome,cpf,matricula,cargo,lotacao,admissao,celular,email)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (dados["nome"],dados["cpf"],dados.get("matricula",""),dados["cargo"],
                     dados.get("lotacao",""),dados.get("admissao",""),dados.get("celular",""),dados.get("email","")))
                fid = cur.fetchone()["id"]
            else:
                cur.execute("""INSERT INTO funcionarios (nome,cpf,matricula,cargo,lotacao,admissao,celular,email)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (dados["nome"],dados["cpf"],dados.get("matricula",""),dados["cargo"],
                     dados.get("lotacao",""),dados.get("admissao",""),dados.get("celular",""),dados.get("email","")))
                fid = cur.lastrowid

        conn.commit()
        return fid
    finally:
        conn.close()


def docs_do_cargo(cargo):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("SELECT doc_id FROM funcao_documentos WHERE cargo=%s ORDER BY doc_id", (cargo,))
        else:
            cur = conn.cursor()
            cur.execute("SELECT doc_id FROM funcao_documentos WHERE cargo=? ORDER BY doc_id", (cargo,))
        rows = cur.fetchall()
        return [dict(r)["doc_id"] for r in rows]
    finally:
        conn.close()


def salvar_docs_cargo(cargo, doc_ids):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("DELETE FROM funcao_documentos WHERE cargo=%s", (cargo,))
            for doc_id in doc_ids:
                cur.execute("INSERT INTO funcao_documentos (cargo,doc_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (cargo, doc_id))
        else:
            cur = conn.cursor()
            cur.execute("DELETE FROM funcao_documentos WHERE cargo=?", (cargo,))
            for doc_id in doc_ids:
                cur.execute("INSERT OR IGNORE INTO funcao_documentos (cargo,doc_id) VALUES (?,?)", (cargo, doc_id))
        conn.commit()
    finally:
        conn.close()


def buscar_cargos():
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()
        cur.execute("SELECT DISTINCT cargo FROM funcionarios WHERE cargo IS NOT NULL AND cargo != '' ORDER BY cargo")
        rows = cur.fetchall()
        return [dict(r)["cargo"] for r in rows]
    finally:
        conn.close()


def criar_lote(descricao=""):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("INSERT INTO lotes (descricao) VALUES (%s) RETURNING id", (descricao,))
            lote_id = cur.fetchone()["id"]
        else:
            cur = conn.cursor()
            cur.execute("INSERT INTO lotes (descricao) VALUES (?)", (descricao,))
            lote_id = cur.lastrowid
        conn.commit()
        return lote_id
    finally:
        conn.close()


def adicionar_ao_lote(lote_id, funcionario_id):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("INSERT INTO lote_funcionarios (lote_id,funcionario_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (lote_id, funcionario_id))
        else:
            cur = conn.cursor()
            cur.execute("INSERT OR IGNORE INTO lote_funcionarios (lote_id,funcionario_id) VALUES (?,?)", (lote_id, funcionario_id))
        conn.commit()
    finally:
        conn.close()


def registrar_envio(dados):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("""INSERT INTO envios (funcionario_id,doc_id,doc_nome,pdf_path,
                autentique_id,link_assinatura,status,enviado_em)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NOW()) RETURNING id""",
                (dados["funcionario_id"],dados["doc_id"],dados.get("doc_nome",""),
                 dados.get("pdf_path",""),dados.get("autentique_id",""),
                 dados.get("link_assinatura",""),dados.get("status","enviado")))
            eid = cur.fetchone()["id"]
        else:
            cur = conn.cursor()
            cur.execute("""INSERT INTO envios (funcionario_id,doc_id,doc_nome,pdf_path,
                autentique_id,link_assinatura,status,enviado_em)
                VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))""",
                (dados["funcionario_id"],dados["doc_id"],dados.get("doc_nome",""),
                 dados.get("pdf_path",""),dados.get("autentique_id",""),
                 dados.get("link_assinatura",""),dados.get("status","enviado")))
            eid = cur.lastrowid
        conn.commit()
        return eid
    finally:
        conn.close()


def listar_lotes():
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()
        cur.execute("""SELECT l.*, COUNT(lf.id) as total_func,
            SUM(CASE WHEN lf.status='enviado' THEN 1 ELSE 0 END) as enviados
            FROM lotes l LEFT JOIN lote_funcionarios lf ON lf.lote_id=l.id
            GROUP BY l.id ORDER BY l.criado_em DESC""")
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    criar_banco()
