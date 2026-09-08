# Logofier Backend

API FastAPI para adicionar logos em PDFs em lote.

## Stack

- **FastAPI** + Uvicorn
- **SQLAlchemy 2.0** + Alembic (migrations)
- **PyMuPDF** (fitz) — processamento de PDFs
- **Pillow** — conversão de imagens (WEBP/SVG/etc → PNG)
- **JWT** + bcrypt — autenticação
- **Cloudflare R2** (S3-compatible) — armazenamento de arquivos

## Setup local

```bash
# Python 3.12 recomendado
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux/Mac

pip install -r requirements-dev.txt

cp .env.example .env            # preencha as variáveis
# IMPORTANTE: gere um JWT_SECRET forte:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"

# Rodar migrations
alembic upgrade head

# Criar um administrador (obrigatório — não há mais admin semeado)
python scripts/create_admin.py --email admin@exemplo.com --name "Admin"

# Iniciar servidor
uvicorn app.main:app --reload
```

## Testes

```bash
pytest tests/ -v
```

Os testes usam SQLite em memória/temporário e mockam o Cloudflare R2 (não precisa de credenciais reais).

## Docker (produção)

Deploy na VM usando `docker compose` (Postgres 16 + backend). Este repositório
já contém o `docker-compose.yml` na raiz — execute tudo a partir daqui:

```bash
# 1. Criar o .env na raiz deste repositório com os segredos (veja .env.example)
cat > .env <<'EOF'
POSTGRES_USER=logofier
POSTGRES_PASSWORD=<senha-forte>
POSTGRES_DB=logofier
JWT_SECRET=<secreto-de-32+-caracteres>
CORS_ORIGINS=["https://seu-dominio.com"]
# OPCIONAL — Cloudflare R2 (sem R2, usa armazenamento local em /tmp/logofier):
# R2_ACCOUNT_ID=...
# R2_ACCESS_KEY_ID=...
# R2_SECRET_ACCESS_KEY=...
# R2_BUCKET_NAME=logofier-files
# R2_PUBLIC_URL=...
EOF

# 2. Subir a stack
docker compose up -d --build
```

O entrypoint do backend roda `alembic upgrade head` automaticamente antes de
iniciar o servidor.

Para criar o administrador inicial (obrigatório — não há admin semeado):

```bash
docker compose exec api python scripts/bootstrap_admin.py
# ou, usando o nome do container:
docker exec -it logofier-api python scripts/bootstrap_admin.py
```

> Os serviços são `postgres` e `api` (containers `logofier-postgres` e
> `logofier-api`). Dados do banco e arquivos enviados ficam em volumes nomeados
> (`logofier_postgres_data` e `logofier_uploads`) — sobrevivem a `docker compose
> down` e rebuild. O `Dockerfile` fica na raiz deste repositório e é usado como
> build context pelo compose. `JWT_SECRET` e `POSTGRES_PASSWORD` são exigidos
> pelo compose (`${VAR:?...}`) — o deploy falha se estiverem ausentes.

## Endpoints principais

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/api/auth/register` | Cadastro |
| POST | `/api/auth/login` | Login → JWT (access + refresh) |
| POST | `/api/auth/refresh` | Refresh-token rotation |
| POST | `/api/auth/logout` | Revoga refresh tokens |
| POST | `/api/jobs` | Criar job (logo + config) |
| POST | `/api/jobs/{id}/files` | Upload dos PDFs |
| POST | `/api/jobs/{id}/process` | Iniciar processamento |
| GET | `/api/jobs/{id}/status` | Status + progresso |
| GET | `/api/jobs/{id}/download` | Baixar ZIP |
| POST | `/api/preview` | Renderizar página de PDF como PNG |
| GET | `/health` | Health check |
