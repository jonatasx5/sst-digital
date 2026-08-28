#!/usr/bin/env python3
"""
Backup SST Digital — baixa todos os dados e PDFs assinados do Railway
e gera um visualizador HTML offline.

Como usar:
  1. Configure as variáveis abaixo (URL, LOGIN, SENHA)
  2. Execute:  python backup_sst.py
  3. Os arquivos ficam na pasta  backup_sst/
  4. Abra  backup_sst/index.html  no navegador — funciona sem internet

Coloque a pasta backup_sst/ no Google Drive para ter cópia na nuvem também.
"""

# ──────────────────────────────────────────────────────────────
# CONFIGURAÇÃO — edite aqui
# ──────────────────────────────────────────────────────────────
API_URL = "https://SEU-APP.railway.app"   # ex: https://sst-digital.railway.app
LOGIN   = "seu_usuario"
SENHA   = "sua_senha"
# ──────────────────────────────────────────────────────────────

import os, sys, json, re, time, datetime, urllib.request, urllib.error

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup_sst")
DOCS_DIR   = os.path.join(BACKUP_DIR, "documentos")

def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")

def _req(path, token=None, method="GET", body=None):
    url = API_URL.rstrip("/") + path
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers
    except Exception as e:
        return 0, str(e).encode(), {}

def login():
    log("Fazendo login...")
    status, body, _ = _req("/api/login", method="POST", body={"login": LOGIN, "senha": SENHA})
    if status != 200:
        print(f"ERRO: login falhou ({status}). Verifique URL, usuário e senha.")
        sys.exit(1)
    data = json.loads(body)
    log(f"Logado como: {data.get('nome', '')} ({data.get('perfil', '')})")
    return data["token"]

def get_json(path, token):
    status, body, _ = _req(path, token=token)
    if status != 200:
        log(f"  AVISO: {path} retornou {status}")
        return []
    return json.loads(body)

def download_pdf(envio_id, destino, token):
    status, body, headers = _req(f"/api/envios/{envio_id}/download", token=token)
    # Pode retornar redirect — se body for pequeno e status 2xx, é PDF
    if status in (200, 206) and len(body) > 100:
        with open(destino, "wb") as f:
            f.write(body)
        return True
    # Tenta seguir redirect manualmente
    if status in (301, 302, 303, 307, 308):
        loc = headers.get("Location") or headers.get("location") or ""
        if loc:
            try:
                with urllib.request.urlopen(loc, timeout=30) as r:
                    data = r.read()
                if len(data) > 100:
                    with open(destino, "wb") as f:
                        f.write(data)
                    return True
            except Exception:
                pass
    return False

def nome_seguro(s):
    return re.sub(r'[\\/*?:"<>|]', '_', (s or "sem_nome")).strip()[:80]

def main():
    os.makedirs(DOCS_DIR, exist_ok=True)

    token = login()

    # ── Funcionários ──────────────────────────────────────────
    log("Baixando funcionários...")
    funcionarios = get_json("/api/funcionarios?limite=2000", token)
    log(f"  {len(funcionarios)} funcionários")

    # ── Empresas ──────────────────────────────────────────────
    log("Baixando empresas...")
    empresas = get_json("/api/empresas", token)
    log(f"  {len(empresas)} empresas")

    # ── Histórico de envios ───────────────────────────────────
    log("Baixando histórico de documentos...")
    envios = get_json("/api/envios?limite=2000", token)
    log(f"  {len(envios)} registros")

    # ── Download dos PDFs assinados ───────────────────────────
    assinados = [e for e in envios if e.get("status") == "signed"]
    log(f"Baixando {len(assinados)} PDFs assinados...")
    for e in assinados:
        func_nome = nome_seguro(e.get("funcionario") or f"id{e.get('funcionario_id','')}")
        doc_nome  = nome_seguro(e.get("doc_nome") or e.get("doc_id") or "documento")
        pasta     = os.path.join(DOCS_DIR, func_nome)
        os.makedirs(pasta, exist_ok=True)
        arquivo   = os.path.join(pasta, f"{doc_nome}_{e['id']}.pdf")
        if os.path.exists(arquivo):
            e["_pdf_local"] = os.path.relpath(arquivo, BACKUP_DIR)
            continue
        ok = download_pdf(e["id"], arquivo, token)
        if ok:
            e["_pdf_local"] = os.path.relpath(arquivo, BACKUP_DIR)
            log(f"  ✓ {func_nome} / {doc_nome}")
        else:
            e["_pdf_local"] = None
            log(f"  ✗ {func_nome} / {doc_nome} (sem PDF disponível)")
        time.sleep(0.3)

    # ── Salvar JSONs ──────────────────────────────────────────
    with open(os.path.join(BACKUP_DIR, "funcionarios.json"), "w", encoding="utf-8") as f:
        json.dump(funcionarios, f, ensure_ascii=False, indent=2)
    with open(os.path.join(BACKUP_DIR, "empresas.json"), "w", encoding="utf-8") as f:
        json.dump(empresas, f, ensure_ascii=False, indent=2)
    with open(os.path.join(BACKUP_DIR, "envios.json"), "w", encoding="utf-8") as f:
        json.dump(envios, f, ensure_ascii=False, indent=2)

    # ── Gerar HTML offline ────────────────────────────────────
    log("Gerando visualizador HTML offline...")
    gerar_html(funcionarios, envios, empresas)

    log("=" * 50)
    log(f"Backup concluído!  →  {BACKUP_DIR}")
    log(f"Abra  backup_sst/index.html  no seu navegador.")

def gerar_html(funcionarios, envios, empresas):
    # Mapeia envios por funcionario_id
    envios_por_func = {}
    for e in envios:
        fid = e.get("funcionario_id")
        if fid:
            envios_por_func.setdefault(fid, []).append(e)

    agora = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

    def badge_status(s):
        m = {"signed": ("✅","#16a34a","#f0fdf4","Assinado"),
             "pending": ("⏳","#b45309","#fefce8","Aguardando"),
             "enviado": ("⏳","#b45309","#fefce8","Aguardando"),
             "pendente": ("⏳","#b45309","#fefce8","Aguardando"),
             "refused": ("❌","#dc2626","#fef2f2","Recusado")}
        ico, cor, bg, label = m.get(s, ("—","#6b7280","#f3f4f6", s or "—"))
        return f'<span style="background:{bg};color:{cor};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">{ico} {label}</span>'

    linhas_func = []
    for f in sorted(funcionarios, key=lambda x: x.get("nome","").upper()):
        docs = envios_por_func.get(f.get("id"), [])
        docs_html = ""
        if docs:
            rows = []
            for e in sorted(docs, key=lambda x: x.get("enviado_em",""), reverse=True):
                pdf_local = e.get("_pdf_local")
                link_plat = e.get("link_assinatura","")
                if pdf_local:
                    acesso = f'<a href="{pdf_local}" target="_blank" style="color:#2563eb;font-size:12px">📄 Abrir PDF</a>'
                elif link_plat:
                    acesso = f'<a href="{link_plat}" target="_blank" style="color:#2563eb;font-size:12px">🔗 Link</a>'
                else:
                    acesso = '<span style="color:#9ca3af;font-size:12px">sem link</span>'
                assinado_em = e.get("assinado_em","") or ""
                data_str = assinado_em[:10] if assinado_em else (e.get("enviado_em","") or "")[:10]
                rows.append(f"""<tr style="border-bottom:1px solid #f3f4f6">
                  <td style="padding:6px 8px;font-size:12px">{e.get('doc_nome') or e.get('doc_id','—')}</td>
                  <td style="padding:6px 8px">{badge_status(e.get('status'))}</td>
                  <td style="padding:6px 8px;font-size:12px;color:#6b7280">{data_str}</td>
                  <td style="padding:6px 8px">{acesso}</td>
                </tr>""")
            docs_html = f"""<table style="width:100%;border-collapse:collapse;margin-top:6px">
              <thead><tr style="background:#f9fafb">
                <th style="padding:5px 8px;font-size:11px;text-align:left;color:#6b7280">Documento</th>
                <th style="padding:5px 8px;font-size:11px;text-align:left;color:#6b7280">Status</th>
                <th style="padding:5px 8px;font-size:11px;text-align:left;color:#6b7280">Data</th>
                <th style="padding:5px 8px;font-size:11px;text-align:left;color:#6b7280">Acesso</th>
              </tr></thead><tbody>{''.join(rows)}</tbody></table>"""
        else:
            docs_html = '<div style="color:#9ca3af;font-size:12px;padding:6px 0">Nenhum documento registrado.</div>'

        # Docs extras manuais
        extras = []
        try:
            extras = json.loads(f.get("docs_extras") or "[]")
        except Exception:
            pass
        for d in extras:
            lk = d.get("link","")
            ac = f'<a href="{lk}" target="_blank" style="color:#2563eb;font-size:12px">🔗 Link</a>' if lk else '<span style="color:#9ca3af;font-size:12px">sem link</span>'
            docs_html += f'<div style="margin-top:4px;font-size:12px">📎 {d.get("nome","—")} — {ac}</div>'

        status_doc = f.get("status_doc","") or ("assinado" if f.get("assinou_doc") else "")
        sd_badge = {"assinado":'<span style="color:#16a34a;font-weight:600">✅ Assinado</span>',
                    "desistente":'<span style="color:#dc2626;font-weight:600">🚫 Desistente</span>'}.get(status_doc,"")
        link_doc = f.get("link_doc","") or ""
        if link_doc:
            sd_badge += f' <a href="{link_doc}" target="_blank" style="color:#2563eb;font-size:12px">🔗 Ver doc</a>'

        linhas_func.append(f"""
        <div class="card" style="margin-bottom:12px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden">
          <div style="background:#f9fafb;padding:12px 16px;display:flex;align-items:center;justify-content:space-between;cursor:pointer"
               onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'':'none'">
            <div>
              <strong style="font-size:14px">{f.get('nome','—')}</strong>
              <span style="color:#6b7280;font-size:12px;margin-left:10px">{f.get('cargo','')}</span>
              <span style="color:#6b7280;font-size:12px;margin-left:8px">· {f.get('lotacao','')}</span>
              <span style="color:#6b7280;font-size:12px;margin-left:8px">· {f.get('empresa','')}</span>
            </div>
            <div style="display:flex;align-items:center;gap:10px">
              {sd_badge}
              <span style="color:#9ca3af;font-size:18px">▾</span>
            </div>
          </div>
          <div style="padding:12px 16px;display:none">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;color:#374151;margin-bottom:10px">
              <div><b>CPF:</b> {f.get('cpf','—')}</div>
              <div><b>Matrícula:</b> {f.get('matricula','—')}</div>
              <div><b>Admissão:</b> {f.get('admissao','—')}</div>
              <div><b>Celular:</b> {f.get('celular','—')}</div>
              <div><b>E-mail:</b> {f.get('email','—')}</div>
            </div>
            <div style="font-weight:600;font-size:12px;color:#374151;margin-bottom:4px">Documentos:</div>
            {docs_html}
          </div>
        </div>""")

    # Estatísticas
    total_func = len(funcionarios)
    total_docs = len(envios)
    total_assin = sum(1 for e in envios if e.get("status") == "signed")
    total_pdfs  = sum(1 for e in envios if e.get("_pdf_local"))

    empresas_list = sorted(set(f.get("empresa","") for f in funcionarios if f.get("empresa")))
    opt_empresas  = "".join(f'<option value="{e}">{e}</option>' for e in empresas_list)

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SST Digital — Backup {agora}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,sans-serif;background:#f3f4f6;color:#111827;padding:16px}}
  h1{{font-size:20px;margin-bottom:4px}}
  .stats{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}}
  .stat{{background:#fff;border-radius:10px;padding:12px 18px;text-align:center;border:1px solid #e5e7eb}}
  .stat b{{display:block;font-size:22px;color:#2563eb}}
  .stat span{{font-size:11px;color:#6b7280}}
  .filtros{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}}
  .filtros input,.filtros select{{padding:7px 10px;border:1px solid #d1d5db;border-radius:8px;font-size:13px}}
  .filtros input{{flex:1;min-width:180px}}
</style>
</head>
<body>
<h1>📋 SST Digital — Backup offline</h1>
<p style="color:#6b7280;font-size:12px">Gerado em {agora} · {total_func} funcionários · {total_docs} documentos · {total_assin} assinados · {total_pdfs} PDFs baixados</p>

<div class="stats">
  <div class="stat"><b>{total_func}</b><span>Funcionários</span></div>
  <div class="stat"><b>{total_docs}</b><span>Documentos</span></div>
  <div class="stat"><b>{total_assin}</b><span>Assinados</span></div>
  <div class="stat"><b>{total_pdfs}</b><span>PDFs locais</span></div>
</div>

<div class="filtros">
  <input type="text" id="busca" placeholder="Buscar por nome, cargo, lotação..." oninput="filtrar()">
  <select id="sel-empresa" onchange="filtrar()">
    <option value="">Todas as empresas</option>
    {opt_empresas}
  </select>
</div>

<div id="lista">{''.join(linhas_func)}</div>

<script>
const cards = Array.from(document.querySelectorAll('#lista > .card'));
function filtrar() {{
  const b = document.getElementById('busca').value.toLowerCase();
  const emp = document.getElementById('sel-empresa').value.toLowerCase();
  cards.forEach(c => {{
    const txt = c.textContent.toLowerCase();
    c.style.display = ((!b || txt.includes(b)) && (!emp || txt.includes(emp))) ? '' : 'none';
  }});
}}
</script>
</body>
</html>"""

    with open(os.path.join(BACKUP_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    main()
