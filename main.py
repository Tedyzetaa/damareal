import asyncio
import json
import os
import random
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date
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
BASE_URL = os.environ.get("BASE_URL", "http://localhost:6500")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")

# Validação obrigatória das variáveis do Supabase e Google (NOVO-04 + Melhoria B)
if not SUPABASE_URL or not SUPABASE_KEY or not GOOGLE_CLIENT_ID:
    raise RuntimeError(
        "Variáveis obrigatórias ausentes: SUPABASE_URL, SUPABASE_ANON_KEY, GOOGLE_CLIENT_ID. "
        "Configure-as no painel do Render ou no arquivo .env."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================================================================
# CONFIGURAÇÃO DO BUCKET PARA AVATARES (NOVO-07)
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

app = FastAPI(title="Damas Real - Server-Side Engine com IA", lifespan=lifespan)

# ================================================================
# RATE LIMITING
# ================================================================
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ================================================================
# CORS
# ================================================================
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
# VALIDAÇÕES
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

# ================================================================
# MODELOS PYDANTIC (Melhoria C)
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
# ENDPOINTS
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
        res   = supabase.table("perfis").select("nick, saldo, foto_url").eq("google_id", google_id).execute()
        row   = res.data[0] if res.data else {}
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
    foto:     UploadFile = File(None),
    dados:    str = Form(...),
    googleId: str = Form(...)
):
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
        ext_final = content_type_map.get(foto.content_type, ext)
        bucket_path = f"avatars/{googleId}{ext_final}"
        foto_bytes = await foto.read()
        supabase.storage.from_("avatars").upload(
            bucket_path, foto_bytes,
            file_options={"content-type": foto.content_type, "upsert": True}  # V3-09
        )
        foto_url_atual = supabase.storage.from_("avatars").get_public_url(bucket_path)

    update_data = {
        "bio": perfil_json.get('bio'),
        "telefone": perfil_json.get('telefone'),
        "cpf": cpf_valor,
        "data_nasc": data_nasc,
        "nick": perfil_json.get('nick')
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
        "partida_id": partida_id,
        "google_id": payload.googleId,
        "tipo": payload.tipo,
        "resultado": payload.resultado,
        "valor": payload.valor,
        "delta_saldo": delta
    }).execute()

    if delta != 0.0:
        supabase.table("perfis").update({"saldo": saldo_atual + delta}).eq("google_id", payload.googleId).execute()

    return {
        "status": "registered",
        "partida_id": partida_id,
        "delta": delta,
        "novo_saldo": round(saldo_atual + delta, 2)
    }

# ================================================================
# ENGINE DE DAMAS (com correção V3-01, V3-03, V3-06)
# ================================================================
def algebraic_to_index(coord: str) -> Tuple[int, int]:
    col = ord(coord[0].upper()) - ord('A')
    row = 8 - int(coord[1])
    return row, col

def index_to_algebraic(row: int, col: int) -> str:
    return f"{chr(ord('A') + col)}{8 - row}"

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
        brancas_vivas = False
        pretas_vivas = False
        
        for linha in tabuleiro:
            for p in linha:
                if p.lower() == 'w': brancas_vivas = True
                if p.lower() == 'b': pretas_vivas = True
                
        if not brancas_vivas: return "b"
        if not pretas_vivas: return "w"

        brancas_tem_movimento = False
        for r in range(8):
            for c in range(8):
                if tabuleiro[r][c].lower() == 'w':
                    if DamasEngine.tem_capturas_obrigatorias_da_peca(tabuleiro, r, c, 'w', regras):
                        brancas_tem_movimento = True
                        break
                    for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                        rt, ct = r + dr, c + dc
                        if 0 <= rt < 8 and 0 <= ct < 8:
                            sucesso, *_ = DamasEngine.validar_e_mover(tabuleiro, r, c, rt, ct, 'w', regras)
                            if sucesso:
                                brancas_tem_movimento = True
                                break
            if brancas_tem_movimento: break

        if not brancas_tem_movimento: return "b"

        pretas_tem_movimento = False
        for r in range(8):
            for c in range(8):
                if tabuleiro[r][c].lower() == 'b':
                    if DamasEngine.tem_capturas_obrigatorias_da_peca(tabuleiro, r, c, 'b', regras):
                        pretas_tem_movimento = True
                        break
                    for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                        rt, ct = r + dr, c + dc
                        if 0 <= rt < 8 and 0 <= ct < 8:
                            sucesso, *_ = DamasEngine.validar_e_mover(tabuleiro, r, c, rt, ct, 'b', regras)
                            if sucesso:
                                pretas_tem_movimento = True
                                break
            if pretas_tem_movimento: break

        if not pretas_tem_movimento: return "w"
        return None

    @staticmethod
    def tem_capturas_obrigatorias_da_peca(board: List[List[str]], r: int, c: int, cor: str, regras: str) -> bool:
        peca = board[r][c]
        if peca == '.' or peca.lower() != cor:
            return False
        is_dama = peca.isupper()
        direcoes = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
        for dr, dc in direcoes:
            # V3-06: Removido bloqueio de direção para peças comuns
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
                else: # Brasileira (Voo longo)
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
        for r in range(8):
            for c in range(8):
                if DamasEngine.tem_capturas_obrigatorias_da_peca(board, r, c, cor, regras):
                    return True
        return False

    @staticmethod
    def obter_todos_movimentos_validos(board, player, regras):
        movimentos = []
        capturas = []
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
                    # Usa continue_capture=True só para candidatos de captura (dist>=2)
                    # Para movimentos simples (dist=1), False permite andar normalmente
                    dist = max(abs(nr - r), abs(nc - c))
                    cc_flag = dist >= 2  # só suprime recursão em capturas, não bloqueia passos simples
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
                if p == 'b':   score += 10 + (7 - r)
                elif p == 'B': score += 35
                elif p == 'w': score -= (10 + r)
                elif p == 'W': score -= 35
        return score

    @staticmethod
    def minimax(board, profundidade, alpha, beta, maximizando, regras):
        if profundidade == 0:
            return DamasEngine.avaliar_tabuleiro(board), None
        player = "b" if maximizando else "w"
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

        # --------------------------------------------------------
        # LÓGICA PARA PEÇAS COMUNS ('w', 'b')
        # --------------------------------------------------------
        if peca in ['w', 'b']:
            # Movimento Simples (Andar 1 casa)
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
                return True, nb, False # <-- CORRIGIDO: Terceiro parâmetro falso (sem multi-capture)
                
            # Movimento de Captura (Pular 2 casas)
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
                    return True, nb, True # <-- CORRIGIDO: True indica que há mais capturas (Combo)
                return True, nb, False    # <-- CORRIGIDO: False indica fim da jogada
                
            return False, board, "Movimento inválido para peça comum."

        # --------------------------------------------------------
        # LÓGICA PARA DAMAS ('W', 'B') - VOO LONGO
        # --------------------------------------------------------
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
                return True, nb, False # <-- CORRIGIDO: Sem capturas pendentes
                
            elif len(caminho) == 1:
                rcap, ccap, pcap = caminho[0]
                if pcap.lower() == player:
                    return False, board, "Você não pode pular a sua própria peça."
                    
                nb = [row[:] for row in board]
                nb[r_from][c_from] = "."
                nb[rcap][ccap] = "."
                nb[r_to][c_to] = peca
                
                if DamasEngine.tem_capturas_obrigatorias_da_peca(nb, r_to, c_to, player, regras):
                    return True, nb, True # <-- CORRIGIDO: Tem combo de Dama
                return True, nb, False    # <-- CORRIGIDO: Fim do turno da Dama
            else:
                return False, board, "Múltiplas peças no caminho diagonal."
                
        return False, board, "Movimento não suportado."

# ================================================================
# GERENCIADOR DE SALAS E WEBSOCKETS
# ================================================================
class GerenciadorSalas:
    def __init__(self):
        self.partidas: Dict[str, dict] = {}
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
        for ws in self.conexoes.get(game_id, []):
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                pass

salas = GerenciadorSalas()

@app.websocket("/ws/partida/{game_id}/{player_color}")
async def websocket_endpoint(websocket: WebSocket, game_id: str, player_color: str):
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

                tem_que_comer = DamasEngine.jogador_tem_capturas_possiveis(partida["board"], player_color, partida["regras"])
                movimento_eh_captura = abs(rf - rt) >= 2
                if tem_que_comer and not movimento_eh_captura:
                    await websocket.send_text(json.dumps({
                        "type": "invalid_move",
                        "message": "Movimento inválido! Você é obrigado a capturar uma peça adversária."
                    }))
                    continue

                ok, nb, motivo = DamasEngine.validar_e_mover(
                    partida["board"], rf, cf, rt, ct, player_color, partida["regras"])
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
            elif msg.get("type") == "config_rules":
                partida["regras"] = msg.get("regras", "brasileira")
                await salas.broadcast(game_id, {
                    "type": "update", "board": partida["board"],
                    "turn": partida["turn"], "regras": partida["regras"]
                })
    except WebSocketDisconnect:
        salas.desconectar(game_id, websocket)

@app.websocket("/ws/ia/{game_id}")
async def websocket_ia_endpoint(websocket: WebSocket, game_id: str):
    await websocket.accept()
    p = {
        "board":      DamasEngine.criar_tabuleiro_inicial(),
        "turn":       "w",
        "regras":     "brasileira",
        "partida_id": str(uuid.uuid4())
    }
    await websocket.send_text(json.dumps({
        "type": "init", "board": p["board"], "turn": p["turn"], "regras": p["regras"]
    }))
    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            if msg.get("type") == "config_rules":
                p["regras"] = msg.get("regras", "brasileira")
                await websocket.send_text(json.dumps({
                    "type": "update", "board": p["board"], "turn": p["turn"], "regras": p["regras"]
                }))
                continue
            # V3-08: verifica turno
            elif msg.get("type") == "move" and p["turn"] == "w":
                irf, icf = algebraic_to_index(msg["from"])
                irt, ict = algebraic_to_index(msg["to"])
                
                tem_que_comer_w = DamasEngine.jogador_tem_capturas_possiveis(p["board"], "w", p["regras"])
                movimento_eh_captura_w = abs(irf - irt) >= 2
                
                if tem_que_comer_w and not movimento_eh_captura_w:
                    await websocket.send_text(json.dumps({
                        "type": "invalid_move", 
                        "message": "Movimento inválido! É obrigado a capturar uma peça adversária."
                    }))
                    continue
                    
                sucesso, novo_board, motivo = DamasEngine.validar_e_mover(
                    p["board"], irf, icf, irt, ict, "w", p["regras"]
                )
                
                if sucesso:
                    p["board"] = novo_board
                    venc = DamasEngine.verificar_fim_de_jogo(p["board"], p["regras"])
                    
                    if venc:
                        await websocket.send_text(json.dumps({
                            "type": "game_over", "winner": venc, "partida_id": p["partida_id"], "board": p["board"]
                        }))
                        continue
                    
                    # Combo check
                    if motivo and movimento_eh_captura_w:
                        await websocket.send_text(json.dumps({
                            "type": "update", "board": p["board"], "turn": "w", "regras": p["regras"],
                            "must_continue": True, "piece": [irt, ict]
                        }))
                    else:
                        # Passa turno para a IA
                        p["turn"] = "b"
                        await websocket.send_text(json.dumps({
                            "type": "update", "board": p["board"], "turn": "b", "regras": p["regras"]
                        }))
                        
                        await asyncio.sleep(0.5)
                        
                        # ============================================================
                        # TURNO DA IA (PRETAS) CORRIGIDO
                        # ============================================================
                        _, mov = DamasEngine.minimax(p["board"], 4, -float('inf'), float('inf'), True, p["regras"])
                        if mov:
                            (irf_b, icf_b), (irt_b, ict_b) = mov
                            
                            # CORREÇÃO CRÍTICA: Começa com continue_capture=False para permitir passos normais!
                            sucesso_b, novo_board_b, tem_combo_b = DamasEngine.validar_e_mover(
                                p["board"], irf_b, icf_b, irt_b, ict_b, "b", p["regras"], continue_capture=False
                            )
                            
                            if sucesso_b:
                                p["board"] = novo_board_b
                                
                                # LOOP DE COMBO AUTOMÁTICO: Se a IA capturar e puder continuar a comer, ela continua!
                                while tem_combo_b:
                                    # Pequeno intervalo entre os saltos para o utilizador ver a animação acontecer
                                    await asyncio.sleep(0.4)
                                    await websocket.send_text(json.dumps({
                                        "type": "update", "board": p["board"], "turn": "b", "regras": p["regras"]
                                    }))
                                    
                                    # Varre os próximos movimentos válidos para encontrar a continuação do combo
                                    proximos_movs = DamasEngine.obter_todos_movimentos_validos(p["board"], "b", p["regras"])
                                    combos_da_peca = [m for m in proximos_movs if m[0] == (irt_b, ict_b)]
                                    
                                    if combos_da_peca:
                                        mov_combo = combos_da_peca[0]
                                        (irf_b, icf_b), (irt_b, ict_b) = mov_combo
                                        # Aqui sim, passamos continue_capture=True porque é a sequência do combo
                                        _, p["board"], tem_combo_b = DamasEngine.validar_e_mover(
                                            p["board"], irf_b, icf_b, irt_b, ict_b, "b", p["regras"], continue_capture=True
                                        )
                                    else:
                                        break
                        
                        # Passa o turno de volta para o jogador humano
                        p["turn"] = "w"
                        venc = DamasEngine.verificar_fim_de_jogo(p["board"], p["regras"])
                        
                        if venc:
                            await websocket.send_text(json.dumps({
                                "type": "game_over", "winner": venc, "partida_id": p["partida_id"], "board": p["board"]
                            }))
                        else:
                            await websocket.send_text(json.dumps({
                                "type": "update", "board": p["board"], "turn": "w", "regras": p["regras"]
                            }))
                else:
                    await websocket.send_text(json.dumps({"type": "invalid_move", "message": novo_board}))
    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 6500))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)