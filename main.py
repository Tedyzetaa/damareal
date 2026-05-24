import asyncio
import json
import os
import random
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, File, UploadFile, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from dotenv import load_dotenv
from supabase import create_client, Client
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

# ================================================================
# CONFIGURAÇÕES DE AMBIENTE
# ================================================================
BASE_URL         = os.environ.get("BASE_URL", "http://localhost:6500")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
SUPABASE_URL     = os.environ.get("SUPABASE_URL")
SUPABASE_KEY     = os.environ.get("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY or not GOOGLE_CLIENT_ID:
    raise RuntimeError(
        "Variáveis obrigatórias ausentes: SUPABASE_URL, SUPABASE_ANON_KEY, GOOGLE_CLIENT_ID. "
        "Configure-as no painel do Render ou no arquivo .env."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================================================================
# BUCKET DE AVATARES
# ================================================================
def ensure_avatars_bucket():
    try:
        supabase.storage.get_bucket("avatars")
        print("Bucket 'avatars' já existe.")
    except Exception:
        try:
            print("Criando bucket 'avatars'...")
            supabase.storage.create_bucket("avatars", {"public": True})
        except Exception as e:
            print(f"Erro ao criar bucket: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_avatars_bucket()
    yield

# ================================================================
# APP + RATE LIMITER + CORS
# ================================================================
app = FastAPI(title="Damas Real - Server-Side Engine com IA", lifespan=lifespan)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        "https://damareal1-2ml7.onrender.com",
        "https://damareal1.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================================================================
# HELPERS UTILITÁRIOS
# ================================================================
def validar_cpf(cpf: str) -> bool:
    cpf = re.sub(r'[^\d]', '', cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in range(9, 11):
        soma = sum(int(cpf[j]) * (i + 1 - j) for j in range(i))
        resto = (soma * 10) % 11
        if resto in (10, 11):
            resto = 0
        if resto != int(cpf[i]):
            return False
    return True

def algebraic_to_index(coord: str) -> Tuple[int, int]:
    col = ord(coord[0].upper()) - ord('A')
    row = 8 - int(coord[1])
    return row, col

def index_to_algebraic(row: int, col: int) -> str:
    return f"{chr(ord('A') + col)}{8 - row}"

# ================================================================
# MODELOS PYDANTIC
# ================================================================
class AuthToken(BaseModel):
    token: str

class RegistrarJogoPayload(BaseModel):
    googleId:   str
    tipo:       str
    resultado:  str
    valor:      float = Field(default=0.0, ge=0)
    partida_id: Optional[str] = None

    @field_validator('tipo', mode='before')
    @classmethod
    def tipo_valido(cls, v):
        if v not in ('ia', 'online', 'aposta', 'apostada'):
            raise ValueError("tipo deve ser 'ia', 'online', 'aposta' ou 'apostada'")
        return 'aposta' if v == 'apostada' else v

    @field_validator('resultado', mode='before')
    @classmethod
    def resultado_valido(cls, v):
        if v not in ('vitoria', 'derrota', 'empate'):
            raise ValueError("resultado deve ser 'vitoria', 'derrota' ou 'empate'")
        return v

# ================================================================
# ENGINE DE DAMAS (completa e inalterada)
# ================================================================
class DamasEngine:
    @staticmethod
    def criar_tabuleiro_inicial() -> List[List[str]]:
        board = [["." for _ in range(8)] for _ in range(8)]
        for r in range(8):
            for c in range(8):
                if (r + c) % 2 == 1:
                    if r < 3:   board[r][c] = "b"
                    elif r > 4: board[r][c] = "w"
        return board

    @staticmethod
    def verificar_fim_de_jogo(tabuleiro: List[List[str]], regras: str) -> Optional[str]:
        brancas_vivas = any(p.lower() == 'w' for linha in tabuleiro for p in linha)
        pretas_vivas  = any(p.lower() == 'b' for linha in tabuleiro for p in linha)

        if not brancas_vivas: return "b"
        if not pretas_vivas:  return "w"

        for cor, retorno in [('w', 'b'), ('b', 'w')]:
            tem_movimento = False
            for r in range(8):
                for c in range(8):
                    if tabuleiro[r][c].lower() == cor:
                        if DamasEngine.tem_capturas_obrigatorias_da_peca(tabuleiro, r, c, cor, regras):
                            tem_movimento = True
                            break
                        for dr, dc in [(-1,-1), (-1,1), (1,-1), (1,1)]:
                            rt, ct = r + dr, c + dc
                            if 0 <= rt < 8 and 0 <= ct < 8:
                                ok, *_ = DamasEngine.validar_e_mover(tabuleiro, r, c, rt, ct, cor, regras)
                                if ok:
                                    tem_movimento = True
                                    break
                    if tem_movimento: break
                if tem_movimento: break
            if not tem_movimento:
                return retorno
        return None

    @staticmethod
    def tem_capturas_obrigatorias_da_peca(board: List[List[str]], r: int, c: int, cor: str, regras: str) -> bool:
        peca = board[r][c]
        if peca == '.' or peca.lower() != cor:
            return False
        is_dama = peca.isupper()
        for dr, dc in [(-1,-1), (-1,1), (1,-1), (1,1)]:
            if not is_dama:
                rm, cm = r + dr, c + dc
                rt, ct = r + 2*dr, c + 2*dc
                if 0 <= rt < 8 and 0 <= ct < 8:
                    if board[rm][cm] != '.' and board[rm][cm].lower() != cor and board[rt][ct] == '.':
                        return True
            else:
                if regras == "americana":
                    rm, cm = r + dr, c + dc
                    rt, ct = r + 2*dr, c + 2*dc
                    if 0 <= rt < 8 and 0 <= ct < 8:
                        if board[rm][cm] != '.' and board[rm][cm].lower() != cor and board[rt][ct] == '.':
                            return True
                else:  # Brasileira — voo longo
                    found_enemy = False
                    nr, nc = r + dr, c + dc
                    while 0 <= nr < 8 and 0 <= nc < 8:
                        if board[nr][nc] == '.':
                            if found_enemy: return True
                        elif board[nr][nc].lower() == cor:
                            break
                        else:
                            if found_enemy: break
                            found_enemy = True
                        nr += dr
                        nc += dc
        return False

    @staticmethod
    def jogador_tem_capturas_possiveis(board: List[List[str]], cor: str, regras: str) -> bool:
        return any(
            DamasEngine.tem_capturas_obrigatorias_da_peca(board, r, c, cor, regras)
            for r in range(8) for c in range(8)
        )

    @staticmethod
    def obter_todos_movimentos_validos(board, player, regras):
        movimentos = []
        capturas   = []
        for r in range(8):
            for c in range(8):
                peca = board[r][c]
                if peca == '.' or peca.lower() != player:
                    continue
                is_dama = peca.isupper()
                candidatos = set()
                for dr, dc in [(-1,-1), (-1,1), (1,-1), (1,1)]:
                    if is_dama and regras == "brasileira":
                        nr, nc = r + dr, c + dc
                        while 0 <= nr < 8 and 0 <= nc < 8:
                            candidatos.add((nr, nc))
                            if board[nr][nc] != '.':
                                nr2, nc2 = nr + dr, nc + dc
                                if 0 <= nr2 < 8 and 0 <= nc2 < 8 and board[nr2][nc2] == '.':
                                    candidatos.add((nr2, nc2))
                                break
                            nr += dr
                            nc += dc
                    else:
                        for dist in [1, 2]:
                            nr, nc = r + dr*dist, c + dc*dist
                            if 0 <= nr < 8 and 0 <= nc < 8:
                                candidatos.add((nr, nc))
                for nr, nc in candidatos:
                    dist    = max(abs(nr - r), abs(nc - c))
                    cc_flag = dist >= 2
                    ok, _, _ = DamasEngine.validar_e_mover(board, r, c, nr, nc, player, regras, continue_capture=cc_flag)
                    if ok:
                        mov = ((r, c), (nr, nc))
                        if abs(nr - r) >= 2:
                            capturas.append(mov)
                        else:
                            movimentos.append(mov)
        return capturas if capturas else movimentos

    @staticmethod
    def avaliar_tabuleiro(board):
        score = 0
        for r in range(8):
            for c in range(8):
                p = board[r][c]
                if   p == 'b': score += 10 + (7 - r)
                elif p == 'B': score += 35
                elif p == 'w': score -= (10 + r)
                elif p == 'W': score -= 35
        return score

    @staticmethod
    def minimax(board, profundidade, alpha, beta, maximizando, regras):
        if profundidade == 0:
            return DamasEngine.avaliar_tabuleiro(board), None
        player     = "b" if maximizando else "w"
        movimentos = DamasEngine.obter_todos_movimentos_validos(board, player, regras)
        if not movimentos:
            return DamasEngine.avaliar_tabuleiro(board), None
        melhor = random.choice(movimentos)
        if maximizando:
            best = -float('inf')
            for mov in movimentos:
                (rf, cf), (rt, ct) = mov
                _, nb, _ = DamasEngine.validar_e_mover(board, rf, cf, rt, ct, "b", regras, continue_capture=True)
                ev, _    = DamasEngine.minimax(nb, profundidade-1, alpha, beta, False, regras)
                if ev > best: best, melhor = ev, mov
                alpha = max(alpha, ev)
                if beta <= alpha: break
            return best, melhor
        else:
            best = float('inf')
            for mov in movimentos:
                (rf, cf), (rt, ct) = mov
                _, nb, _ = DamasEngine.validar_e_mover(board, rf, cf, rt, ct, "w", regras, continue_capture=True)
                ev, _    = DamasEngine.minimax(nb, profundidade-1, alpha, beta, True, regras)
                if ev < best: best, melhor = ev, mov
                beta = min(beta, ev)
                if beta <= alpha: break
            return best, melhor

    @staticmethod
    def validar_e_mover(board, r_from, c_from, r_to, c_to, player, regras, continue_capture=False):
        peca = board[r_from][c_from]
        if peca == "." or peca.lower() != player:
            return False, board, "Você não tem uma peça nessa casa."
        if board[r_to][c_to] != ".":
            return False, board, "A casa de destino não está vazia."
        if (r_to + c_to) % 2 == 0:
            return False, board, "Movimento inválido: peças só jogam nas casas escuras."

        dr = r_to - r_from
        dc = c_to - c_from

        if peca in ['w', 'b']:
            if abs(dr) == 1 and abs(dc) == 1:
                if continue_capture:
                    return False, board, "Precisa continuar capturando."
                if peca == 'w' and dr >= 0:
                    return False, board, "Peças brancas comuns só andam para a frente."
                if peca == 'b' and dr <= 0:
                    return False, board, "Peças pretas comuns só andam para a frente."
                nb = [row[:] for row in board]
                nb[r_from][c_from] = "."
                nb[r_to][c_to] = 'W' if (peca == 'w' and r_to == 0) else ('B' if (peca == 'b' and r_to == 7) else peca)
                return True, nb, False
            elif abs(dr) == 2 and abs(dc) == 2:
                rm, cm = r_from + dr//2, c_from + dc//2
                pc = board[rm][cm]
                if pc == "." or pc.lower() == player:
                    return False, board, "Não há peça adversária para capturar."
                nb = [row[:] for row in board]
                nb[r_from][c_from] = "."
                nb[rm][cm] = "."
                nb[r_to][c_to] = 'W' if (peca == 'w' and r_to == 0) else ('B' if (peca == 'b' and r_to == 7) else peca)
                if DamasEngine.tem_capturas_obrigatorias_da_peca(nb, r_to, c_to, player, regras):
                    return True, nb, True
                return True, nb, False
            return False, board, "Movimento inválido para peça comum."

        elif peca in ['W', 'B']:
            if abs(dr) != abs(dc):
                return False, board, "Damas só se movem na diagonal."
            sr = 1 if dr > 0 else -1
            sc = 1 if dc > 0 else -1
            caminho = []
            cr, cc = r_from + sr, c_from + sc
            while cr != r_to:
                if board[cr][cc] != ".":
                    caminho.append((cr, cc, board[cr][cc]))
                cr += sr
                cc += sc

            if len(caminho) == 0:
                if continue_capture:
                    return False, board, "Dama precisa continuar capturando."
                nb = [row[:] for row in board]
                nb[r_from][c_from] = "."
                nb[r_to][c_to] = peca
                return True, nb, False
            elif len(caminho) == 1:
                rcap, ccap, pcap = caminho[0]
                if pcap.lower() == player:
                    return False, board, "Você não pode pular a sua própria peça."
                nb = [row[:] for row in board]
                nb[r_from][c_from] = "."
                nb[rcap][ccap] = "."
                nb[r_to][c_to] = peca
                if DamasEngine.tem_capturas_obrigatorias_da_peca(nb, r_to, c_to, player, regras):
                    return True, nb, True
                return True, nb, False
            else:
                return False, board, "Múltiplas peças no caminho diagonal."
        return False, board, "Movimento não suportado."

# ================================================================
# GERENCIADOR DE SALAS
# ================================================================
class GerenciadorSalas:
    def __init__(self):
        self.partidas: Dict[str, dict]          = {}
        self.conexoes: Dict[str, List[WebSocket]] = {}

    async def conectar(self, game_id, ws):
        await ws.accept()
        self.conexoes.setdefault(game_id, []).append(ws)

    def desconectar(self, game_id, ws):
        if game_id in self.conexoes:
            try:
                self.conexoes[game_id].remove(ws)
            except ValueError:
                pass

    async def broadcast(self, game_id, msg):
        for ws in list(self.conexoes.get(game_id, [])):
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                pass

salas = GerenciadorSalas()

# ================================================================
# ENDPOINTS REST
# ================================================================
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/auth/google")
@limiter.limit("10/minute")
async def auth_google(request: Request, payload: AuthToken):
    try:
        id_info = id_token.verify_oauth2_token(
            payload.token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")

    google_id      = id_info['sub']
    email_google   = id_info.get('email', '')
    nome_google    = id_info.get('name', 'Jogador Real')
    picture_google = id_info.get('picture', '')

    try:
        supabase.table("perfis").upsert(
            {"google_id": google_id, "nome": nome_google,
             "email": email_google, "foto_url": picture_google},
            on_conflict="google_id"
        ).execute()
        res = supabase.table("perfis").select("nick, saldo, foto_url").eq("google_id", google_id).execute()
        row = res.data[0] if res.data else {}
    except Exception as e:
        print(f"[SUPABASE ERROR] auth_google: {e}")
        raise HTTPException(status_code=500, detail=f"Erro no banco de dados: {str(e)}")

    nick       = row.get("nick")
    saldo      = row.get("saldo") or 0.0
    foto_final = row.get("foto_url") or picture_google

    return {
        "status": "authenticated", "google_id": google_id,
        "email": email_google, "name": nome_google,
        "nick": nick, "picture": foto_final, "saldo": round(saldo, 2),
    }

@app.post("/update-profile")
async def update_profile(
    request: Request,
    foto:     UploadFile = File(None),
    dados:    str = Form(...),
    googleId: str = Form(...)
):
    # Verificação básica de autenticação (Bug 8)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            id_info = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)
            if id_info['sub'] != googleId:
                raise HTTPException(status_code=403, detail="Não autorizado a alterar este perfil.")
        except Exception:
             raise HTTPException(status_code=401, detail="Token inválido.")

    perfil_json = json.loads(dados)

    data_nasc = perfil_json.get("dataNascimento")
    if data_nasc:
        born  = date.fromisoformat(data_nasc)
        today = date.today()
        age   = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        if age < 18:
            raise HTTPException(status_code=400, detail="Usuário menor de idade.")

    cpf_valor = perfil_json.get("cpf", "")
    if cpf_valor and not validar_cpf(cpf_valor):
        raise HTTPException(status_code=400, detail="CPF inválido.")

    foto_url_atual = None
    if foto and foto.filename:
        EXTENSOES_PERMITIDAS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
        content_type_map = {
            'image/jpeg': '.jpg', 'image/png': '.png',
            'image/gif': '.gif', 'image/webp': '.webp'
        }
        ext = os.path.splitext(foto.filename)[1].lower()
        if ext not in EXTENSOES_PERMITIDAS:
            raise HTTPException(status_code=400, detail="Formato de imagem não permitido.")
        ext_final   = content_type_map.get(foto.content_type, ext)
        bucket_path = f"avatars/{googleId}{ext_final}"
        foto_bytes  = await foto.read()
        supabase.storage.from_("avatars").upload(
            bucket_path, foto_bytes,
            file_options={"content-type": foto.content_type, "upsert": True}
        )
        foto_url_atual = supabase.storage.from_("avatars").get_public_url(bucket_path)

    update_data = {
        "bio":      perfil_json.get('bio'),
        "telefone": perfil_json.get('telefone'),
        "cpf":      cpf_valor,
        "data_nasc": data_nasc,
        "nick":     perfil_json.get('nick')
    }
    if foto_url_atual:
        update_data["foto_url"] = foto_url_atual

    supabase.table("perfis").update(update_data).eq("google_id", googleId).execute()
    return {"status": "Perfil salvo com sucesso!", "foto_url": foto_url_atual}

@app.get("/get-profile/{google_id}")
async def get_profile(google_id: str):
    res_profile = supabase.table("perfis").select("*").eq("google_id", google_id).execute()
    if not res_profile.data:
        return {}
    data = res_profile.data[0]
    res_hist = supabase.table("historico").select("*").eq("google_id", google_id).order("created_at", desc=True).execute()
    data['historico'] = res_hist.data
    return data

@app.post("/registrar-jogo")
async def registrar_jogo(payload: RegistrarJogoPayload):
    partida_id = payload.partida_id or str(uuid.uuid4())

    res_check = supabase.table("historico").select("id").eq("partida_id", partida_id).execute()
    if res_check.data:
        return {"status": "already_registered", "partida_id": partida_id}

    res_user = supabase.table("perfis").select("saldo").eq("google_id", payload.googleId).execute()
    if not res_user.data:
        raise HTTPException(status_code=404, detail="Jogador não encontrado.")

    saldo_atual = res_user.data[0]["saldo"]
    delta = 0.0
    if payload.resultado == "vitoria":
        delta = +payload.valor
    elif payload.resultado == "derrota":
        delta = -payload.valor
        if saldo_atual + delta < 0:
            raise HTTPException(status_code=422, detail=f"Saldo insuficiente. R$ {saldo_atual:.2f}")

    supabase.table("historico").insert({
        "partida_id":  partida_id,
        "google_id":   payload.googleId,
        "tipo":        payload.tipo,
        "resultado":   payload.resultado,
        "valor":       payload.valor,
        "delta_saldo": delta
    }).execute()

    if delta != 0.0:
        supabase.table("perfis").update({"saldo": saldo_atual + delta}).eq("google_id", payload.googleId).execute()

    return {
        "status":     "registered",
        "partida_id": partida_id,
        "delta":      delta,
        "novo_saldo": round(saldo_atual + delta, 2)
    }

# ================================================================
# AUXILIARES DE MATCHMAKING VIA SUPABASE
# ================================================================
async def inserir_na_fila(jogador_id: str):
    supabase.table("fila_espera").insert({
        "jogador_id": jogador_id,
        "status":     "aguardando"
    }).execute()

async def remover_da_fila(jogador_id: str):
    supabase.table("fila_espera").delete() \
        .eq("jogador_id", jogador_id) \
        .execute()

async def buscar_oponente(jogador_id: str) -> Optional[dict]:
    resp = supabase.table("fila_espera") \
        .select("*") \
        .eq("status", "aguardando") \
        .neq("jogador_id", jogador_id) \
        .order("created_at", desc=False) \
        .limit(1) \
        .execute()
    return resp.data[0] if resp.data else None

async def obter_perfil_basico(jogador_id: str) -> dict:
    resp = supabase.table("perfis") \
        .select("nick, nome, foto_url") \
        .eq("google_id", jogador_id) \
        .execute()
    if resp.data:
        row = resp.data[0]
        return {
            "nick":    row.get("nick") or row.get("nome") or "Jogador",
            "picture": row.get("foto_url") or ""
        }
    return {"nick": "Desconhecido", "picture": ""}

# ================================================================
# WEBSOCKET — LOBBY (MATCHMAKING)
# ================================================================
lobby_connections: Dict[str, WebSocket] = {}
match_info: Dict[str, dict] = {}
lobby_lock = asyncio.Lock()

@app.websocket("/ws/lobby")
async def websocket_lobby_endpoint(websocket: WebSocket, google_id: str = None):
    if not google_id:
        await websocket.close(code=1008, reason="google_id é obrigatório")
        return

    await websocket.accept()
    async with lobby_lock:
        await remover_da_fila(google_id)
        match_info.pop(google_id, None)
        await inserir_na_fila(google_id)
        lobby_connections[google_id] = websocket

    try:
        while True:
            # Verifica mensagem de cancelamento
            try:
                msg  = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                data = json.loads(msg)
                if data.get("type") == "cancel":
                    async with lobby_lock:
                        await remover_da_fila(google_id)
                        lobby_connections.pop(google_id, None)
                        match_info.pop(google_id, None)
                    await websocket.close(code=1000, reason="Cancelado")
                    return
            except asyncio.TimeoutError:
                pass

            # 1. Verifica se já foi pareado por outro jogador (polling do próprio status)
            consulta = supabase.table("fila_espera") \
                .select("status, partida_id") \
                .eq("jogador_id", google_id) \
                .execute()
            if consulta.data and consulta.data[0]["status"] == "pareado":
                partida_id = consulta.data[0]["partida_id"]
                # Busca a cor que foi atribuída (armazenada no match_info pelo outro jogador)
                info = match_info.pop(google_id, None)
                cor = info["color"] if (info and "color" in info) else 'b' # Se o outro é 'w', eu sou 'b'
                # Busca o nick do oponente (opcional)
                resp_op = supabase.table("fila_espera") \
                    .select("jogador_id") \
                    .eq("partida_id", partida_id) \
                    .neq("jogador_id", google_id) \
                    .execute()
                opp_id = resp_op.data[0]["jogador_id"] if resp_op.data else None
                opp_perfil = await obter_perfil_basico(opp_id) if opp_id else {"nick": "Adversário", "picture": ""}
                await websocket.send_text(json.dumps({
                    "type": "matched",
                    "game_id": partida_id,
                    "color": cor,
                    "opponent_nick":    opp_perfil["nick"],
                    "opponent_picture": opp_perfil["picture"]
                }))
                async with lobby_lock:
                    await remover_da_fila(google_id)
                    lobby_connections.pop(google_id, None)
                await websocket.close()
                return

            # 2. Tenta encontrar um oponente e parear
            async with lobby_lock:
                oponente = await buscar_oponente(google_id)
                if not oponente:
                    continue

                # Reserva atômica do oponente
                reserva = supabase.table("fila_espera") \
                    .update({"status": "emparelhando"}) \
                    .eq("jogador_id", oponente["jogador_id"]) \
                    .eq("status", "aguardando") \
                    .execute()
                if not reserva.data:
                    continue  # oponente já foi pego

                # Cores aleatórias
                cores = ['w', 'b']
                random.shuffle(cores)
                cor_atual = cores[0]
                cor_op = cores[1]

                # Cria sala
                game_id = f"online_{uuid.uuid4().hex[:8]}"
                salas.partidas[game_id] = {
                    "board":      DamasEngine.criar_tabuleiro_inicial(),
                    "turn":       "w",
                    "regras":     "brasileira",
                    "partida_id": str(uuid.uuid4())
                }

                # Atualiza fila
                supabase.table("fila_espera") \
                    .update({"status": "pareado", "partida_id": game_id}) \
                    .eq("jogador_id", google_id) \
                    .execute()
                supabase.table("fila_espera") \
                    .update({"status": "pareado", "partida_id": game_id}) \
                    .eq("jogador_id", oponente["jogador_id"]) \
                    .execute()

                perfil_op   = await obter_perfil_basico(oponente["jogador_id"])
                perfil_self = await obter_perfil_basico(google_id)

                msg_self = {
                    "type": "matched",
                    "game_id": game_id,
                    "color": cor_atual,
                    "opponent_nick":    perfil_op["nick"],
                    "opponent_picture": perfil_op["picture"]
                }
                msg_opp = {
                    "type": "matched",
                    "game_id": game_id,
                    "color": cor_op,
                    "opponent_nick":    perfil_self["nick"],
                    "opponent_picture": perfil_self["picture"]
                }

                # Notifica oponente se estiver conectado, senão guarda no match_info
                opp_ws = lobby_connections.pop(oponente["jogador_id"], None)
                if opp_ws:
                    try:
                        await opp_ws.send_text(json.dumps(msg_opp))
                        await opp_ws.close()
                    except:
                        match_info[oponente["jogador_id"]] = msg_opp
                else:
                    match_info[oponente["jogador_id"]] = msg_opp

                # Notifica jogador atual
                lobby_connections.pop(google_id, None)
                await websocket.send_text(json.dumps(msg_self))
                await websocket.close()
                return

    except WebSocketDisconnect:
        async with lobby_lock:
            await remover_da_fila(google_id)
            lobby_connections.pop(google_id, None)
            match_info.pop(google_id, None)

# ================================================================
# WEBSOCKET — PARTIDA ONLINE
# ================================================================
@app.websocket("/ws/partida/{game_id}/{player_color}")
async def websocket_partida_endpoint(websocket: WebSocket, game_id: str, player_color: str):
    await salas.conectar(game_id, websocket)
    if game_id not in salas.partidas:
        salas.partidas[game_id] = {
            "board":      DamasEngine.criar_tabuleiro_inicial(),
            "turn":       "w",
            "regras":     "brasileira",
            "partida_id": str(uuid.uuid4())
        }
    partida = salas.partidas[game_id]
    await websocket.send_text(json.dumps({
        "type": "init", "board": partida["board"],
        "turn": partida["turn"], "regras": partida["regras"]
    }))
    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            if msg.get("type") == "move" and partida["turn"] == player_color:
                rf, cf = algebraic_to_index(msg["from"])
                rt, ct = algebraic_to_index(msg["to"])

                tem_que_comer = DamasEngine.jogador_tem_capturas_possiveis(
                    partida["board"], player_color, partida["regras"]
                )
                movimento_eh_captura = abs(rf - rt) >= 2
                if tem_que_comer and not movimento_eh_captura:
                    await websocket.send_text(json.dumps({
                        "type":    "invalid_move",
                        "message": "Movimento inválido! Você é obrigado a capturar uma peça adversária."
                    }))
                    continue

                ok, nb, motivo = DamasEngine.validar_e_mover(
                    partida["board"], rf, cf, rt, ct, player_color, partida["regras"]
                )
                if ok:
                    partida["board"] = nb
                    if motivo:
                        await salas.broadcast(game_id, {
                            "type": "update", "board": nb,
                            "turn": player_color, "regras": partida["regras"],
                            "must_continue": True, "piece": [rt, ct]
                        })
                    else:
                        partida["turn"] = "b" if player_color == "w" else "w"
                        venc = DamasEngine.verificar_fim_de_jogo(nb, partida["regras"])
                        if venc:
                            await salas.broadcast(game_id, {
                                "type": "game_over", "winner": venc,
                                "partida_id": partida["partida_id"], "board": nb
                            })
                        else:
                            await salas.broadcast(game_id, {
                                "type": "update", "board": nb,
                                "turn": partida["turn"], "regras": partida["regras"]
                            })
                else:
                    await websocket.send_text(json.dumps({"type": "invalid_move", "message": motivo}))
            elif msg.get("type") == "chat":
                nick = str(msg.get("nick", "Jogador"))[:30]
                text = str(msg.get("text", ""))[:200].strip()
                sender_id = str(msg.get("sender_id", ""))
                if text:
                    await salas.broadcast(game_id, {
                        "type": "chat",
                        "nick": nick,
                        "text": text,
                        "sender_id": sender_id,
                        "timestamp": datetime.now().strftime("%H:%M")
                    })
            elif msg.get("type") == "config_rules":
                partida["regras"] = msg.get("regras", "brasileira")
                await salas.broadcast(game_id, {
                    "type": "update", "board": partida["board"],
                    "turn": partida["turn"], "regras": partida["regras"]
                })
    except WebSocketDisconnect:
        await salas.broadcast(game_id, {
            "type": "opponent_left",
            "message": "O adversário se desconectou."
        })
        salas.desconectar(game_id, websocket)

# ================================================================
# HELPER PARA EXECUTAR TURNO DA IA (combo automático)
# ================================================================
async def _executar_turno_ia(websocket: WebSocket, p: dict):
    ia_color = p["ia_color"]
    player_color = p["player_color"]

    _, mov = DamasEngine.minimax(
        p["board"], 4, -float('inf'), float('inf'),
        ia_color == "b", p["regras"]
    )
    if not mov:
        return

    (irf, icf), (irt, ict) = mov
    sucesso, novo_board, tem_combo = DamasEngine.validar_e_mover(
        p["board"], irf, icf, irt, ict, ia_color, p["regras"], continue_capture=False
    )
    if not sucesso:
        return

    p["board"] = novo_board
    while tem_combo:
        await asyncio.sleep(0.4)
        await websocket.send_text(json.dumps({
            "type": "update", "board": p["board"],
            "turn": ia_color, "regras": p["regras"]
        }))
        proximos = DamasEngine.obter_todos_movimentos_validos(p["board"], ia_color, p["regras"])
        combos = [m for m in proximos if m[0] == (irt, ict)]
        if combos:
            (irf, icf), (irt, ict) = combos[0]
            _, p["board"], tem_combo = DamasEngine.validar_e_mover(
                p["board"], irf, icf, irt, ict, ia_color, p["regras"], continue_capture=True
            )
        else:
            break

    p["turn"] = player_color
    venc = DamasEngine.verificar_fim_de_jogo(p["board"], p["regras"])
    if venc:
        await websocket.send_text(json.dumps({
            "type": "game_over", "winner": venc,
            "partida_id": p["partida_id"], "board": p["board"]
        }))
    else:
        await websocket.send_text(json.dumps({
            "type": "update", "board": p["board"],
            "turn": player_color, "regras": p["regras"]
        }))

# ================================================================
# WEBSOCKET — IA (aceita cor do jogador)
# ================================================================
@app.websocket("/ws/ia/{game_id}/{player_color}")
async def websocket_ia_endpoint(websocket: WebSocket, game_id: str, player_color: str = "w"):
    if player_color not in ("w", "b"):
        player_color = "w"
    ia_color = "b" if player_color == "w" else "w"

    await websocket.accept()
    p = {
        "board":        DamasEngine.criar_tabuleiro_inicial(),
        "turn":         "w",
        "regras":       "brasileira",
        "partida_id":   str(uuid.uuid4()),
        "player_color": player_color,
        "ia_color":     ia_color
    }

    await websocket.send_text(json.dumps({
        "type": "init", "board": p["board"], "turn": p["turn"], "regras": p["regras"]
    }))

    # Se o jogador é preto, a IA (brancas) começa
    if player_color == "b":
        await asyncio.sleep(0.5)
        await _executar_turno_ia(websocket, p)

    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            if msg.get("type") == "config_rules":
                p["regras"] = msg.get("regras", "brasileira")
                await websocket.send_text(json.dumps({
                    "type": "update", "board": p["board"],
                    "turn": p["turn"], "regras": p["regras"]
                }))
                continue

            if msg.get("type") == "move" and p["turn"] == player_color:
                irf, icf = algebraic_to_index(msg["from"])
                irt, ict = algebraic_to_index(msg["to"])

                tem_que_comer = DamasEngine.jogador_tem_capturas_possiveis(p["board"], player_color, p["regras"])
                eh_captura = abs(irf - irt) >= 2

                if tem_que_comer and not eh_captura:
                    await websocket.send_text(json.dumps({
                        "type": "invalid_move",
                        "message": "Movimento inválido! É obrigado a capturar."
                    }))
                    continue

                sucesso, novo_board, motivo = DamasEngine.validar_e_mover(
                    p["board"], irf, icf, irt, ict, player_color, p["regras"]
                )
                if not sucesso:
                    await websocket.send_text(json.dumps({"type": "invalid_move", "message": motivo}))
                    continue

                p["board"] = novo_board
                venc = DamasEngine.verificar_fim_de_jogo(p["board"], p["regras"])
                if venc:
                    await websocket.send_text(json.dumps({
                        "type": "game_over", "winner": venc,
                        "partida_id": p["partida_id"], "board": p["board"]
                    }))
                    continue

                if motivo and eh_captura:
                    await websocket.send_text(json.dumps({
                        "type": "update", "board": p["board"],
                        "turn": player_color, "regras": p["regras"],
                        "must_continue": True, "piece": [irt, ict]
                    }))
                else:
                    p["turn"] = ia_color
                    await websocket.send_text(json.dumps({
                        "type": "update", "board": p["board"],
                        "turn": ia_color, "regras": p["regras"]
                    }))
                    await asyncio.sleep(0.5)
                    await _executar_turno_ia(websocket, p)

    except WebSocketDisconnect:
        pass

# ================================================================
# PONTO DE ENTRADA
# ================================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 6500))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)