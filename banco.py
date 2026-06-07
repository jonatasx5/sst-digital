"""
banco.py - Banco de dados com suporte a PostgreSQL e SQLite

Usa PostgreSQL quando DATABASE_URL está disponível (Railway)
Usa SQLite como fallback local
"""

import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Detecta se usa PostgreSQL ou SQLite
USE_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith(("postgres://", "postgresql://")))

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
        query = query.replace("INSERT OR IGNORE", "INSERT ON CONFLICT DO NOTHING")
        query = query.replace("OR IGNORE", "ON CONFLICT DO NOTHING")

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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS modelos (
                    pk SERIAL PRIMARY KEY,
                    id TEXT NOT NULL,
                    nome TEXT NOT NULL,
                    conteudo BYTEA,
                    cargo TEXT DEFAULT NULL,
                    criado_em TIMESTAMP DEFAULT NOW(),
                    UNIQUE(id, cargo)
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
            cur.execute("""CREATE TABLE IF NOT EXISTS modelos (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL,
                nome TEXT NOT NULL,
                conteudo BLOB,
                cargo TEXT DEFAULT NULL,
                criado_em TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(id, cargo))""")
            conn.commit()

        print(f"OK Banco criado ({'PostgreSQL' if USE_POSTGRES else 'SQLite'})")
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
            # Verifica CPF duplicado em outro registro antes de UPDATE
            fid_check = dados.get("id")
            if fid_check:
                cur.execute("SELECT id FROM funcionarios WHERE cpf=%s AND id<>%s", (dados["cpf"], fid_check))
                if cur.fetchone():
                    raise ValueError(f"CPF {dados['cpf']} já está cadastrado para outro funcionário.")
        else:
            cur = conn.cursor()
            fid_check = dados.get("id")
            if fid_check:
                cur.execute("SELECT id FROM funcionarios WHERE cpf=? AND id<>?", (dados["cpf"], fid_check))
                if cur.fetchone():
                    raise ValueError(f"CPF {dados['cpf']} já está cadastrado para outro funcionário.")

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
    from config import KIT_PADRAO
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("SELECT doc_id FROM funcao_documentos WHERE cargo=%s ORDER BY doc_id", (cargo,))
        else:
            cur = conn.cursor()
            cur.execute("SELECT doc_id FROM funcao_documentos WHERE cargo=? ORDER BY doc_id", (cargo,))
        rows = cur.fetchall()
        docs_cargo = [dict(r)["doc_id"] for r in rows]
        # Sempre inclui o Kit Padrão, sem duplicar
        todos = KIT_PADRAO + [d for d in docs_cargo if d not in KIT_PADRAO]
        return todos
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
        cur.execute("""SELECT l.id, l.descricao, l.criado_em, l.status,
            COUNT(lf.id) as total_func,
            SUM(CASE WHEN lf.status='enviado' THEN 1 ELSE 0 END) as enviados
            FROM lotes l LEFT JOIN lote_funcionarios lf ON lf.lote_id=l.id
            GROUP BY l.id, l.descricao, l.criado_em, l.status
            ORDER BY l.criado_em DESC""")
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def salvar_modelo(doc_id: str, nome: str, conteudo_bytes: bytes, cargo: str = None):
    """Salva ou atualiza um modelo .docx no banco."""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT pk FROM modelos WHERE id=%s AND cargo IS NOT DISTINCT FROM %s", (doc_id, cargo))
            existe = cur.fetchone()
            if existe:
                cur.execute("UPDATE modelos SET nome=%s, conteudo=%s WHERE id=%s AND cargo IS NOT DISTINCT FROM %s",
                            (nome, conteudo_bytes, doc_id, cargo))
            else:
                cur.execute("INSERT INTO modelos (id, nome, conteudo, cargo) VALUES (%s,%s,%s,%s)",
                            (doc_id, nome, conteudo_bytes, cargo))
        else:
            cur = conn.cursor()
            if cargo is None:
                cur.execute("SELECT pk FROM modelos WHERE id=? AND cargo IS NULL", (doc_id,))
            else:
                cur.execute("SELECT pk FROM modelos WHERE id=? AND cargo=?", (doc_id, cargo))
            existe = cur.fetchone()
            if existe:
                if cargo is None:
                    cur.execute("UPDATE modelos SET nome=?, conteudo=? WHERE id=? AND cargo IS NULL",
                                (nome, conteudo_bytes, doc_id))
                else:
                    cur.execute("UPDATE modelos SET nome=?, conteudo=? WHERE id=? AND cargo=?",
                                (nome, conteudo_bytes, doc_id, cargo))
            else:
                cur.execute("INSERT INTO modelos (id, nome, conteudo, cargo) VALUES (?,?,?,?)",
                            (doc_id, nome, conteudo_bytes, cargo))
        conn.commit()
    finally:
        conn.close()


def buscar_modelo(doc_id: str, cargo: str = None) -> bytes | None:
    """
    Retorna bytes do modelo .docx.
    Tenta cargo específico primeiro, depois fallback para modelo geral (cargo IS NULL).
    """
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            # Tenta específico de cargo primeiro
            if cargo:
                cur.execute("SELECT conteudo FROM modelos WHERE id=%s AND cargo=%s", (doc_id, cargo))
                row = cur.fetchone()
                if row and row[0]:
                    return bytes(row[0])
            # Fallback geral
            cur.execute("SELECT conteudo FROM modelos WHERE id=%s AND cargo IS NULL", (doc_id,))
            row = cur.fetchone()
            return bytes(row[0]) if row and row[0] else None
        else:
            cur = conn.cursor()
            if cargo:
                cur.execute("SELECT conteudo FROM modelos WHERE id=? AND cargo=?", (doc_id, cargo))
                row = cur.fetchone()
                if row and row[0]:
                    return bytes(row[0])
            cur.execute("SELECT conteudo FROM modelos WHERE id=? AND cargo IS NULL", (doc_id,))
            row = cur.fetchone()
            return bytes(row[0]) if row and row[0] else None
    finally:
        conn.close()


def listar_modelos() -> list:
    """Retorna lista de modelos com id, nome, cargo e tem_conteudo (bool)."""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(_psycopg2_extras.RealDictCursor) if _psycopg2_extras else conn.cursor()
            cur.execute("SELECT id, nome, cargo, (conteudo IS NOT NULL) AS tem_conteudo FROM modelos ORDER BY id, cargo")
        else:
            cur = conn.cursor()
            cur.execute("SELECT id, nome, cargo, (conteudo IS NOT NULL) AS tem_conteudo FROM modelos ORDER BY id, cargo")
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def deletar_modelo(doc_id: str, cargo: str = None):
    """Remove um modelo do banco."""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("DELETE FROM modelos WHERE id=%s AND cargo IS NOT DISTINCT FROM %s", (doc_id, cargo))
        else:
            cur = conn.cursor()
            if cargo is None:
                cur.execute("DELETE FROM modelos WHERE id=? AND cargo IS NULL", (doc_id,))
            else:
                cur.execute("DELETE FROM modelos WHERE id=? AND cargo=?", (doc_id, cargo))
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    criar_banco()
