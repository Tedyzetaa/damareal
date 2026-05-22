// ============================================================
// ESTADO GLOBAL (sem duplicatas)
// ============================================================
let ws             = null;
let selectedSquare = null;
let currentBoard   = [];
let meuTurno       = false;
let modoAtual      = '';
let capturedWhite  = 0;   // peças pretas capturadas pelas brancas
let capturedBlack  = 0;   // peças brancas capturadas pelas pretas
let lastBoard      = null;

// ============================================================
// TOAST (substitui alert())
// ============================================================
function showToast(msg, type = 'info', duration = 3500) {
    const icons = { error: '✕', success: '✓', info: '◆' };
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<span class="toast-icon">${icons[type] || '◆'}</span><span class="toast-msg">${msg}</span>`;
    container.appendChild(el);
    setTimeout(() => {
        el.style.animation = 'toastOut 0.3s cubic-bezier(0.22,1,0.36,1) forwards';
        setTimeout(() => el.remove(), 300);
    }, duration);
}

// ============================================================
// NAVEGAÇÃO
// ============================================================
function mudarTela(screenId) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(screenId).classList.add('active');
}

// ============================================================
// COORDENADAS DO TABULEIRO
// ============================================================
function renderizarCoordenadas() {
    const letras  = document.getElementById('coordLetters');
    const numeros = document.getElementById('coordNumbers');
    letras.innerHTML  = '';
    numeros.innerHTML = '';
    const cols = ['A','B','C','D','E','F','G','H'];
    cols.forEach(l => {
        const d = document.createElement('div');
        d.className = 'coord-label';
        d.textContent = l;
        letras.appendChild(d);
    });
    for (let i = 8; i >= 1; i--) {
        const d = document.createElement('div');
        d.className = 'coord-label';
        d.textContent = i;
        numeros.appendChild(d);
    }
}

// ============================================================
// RENDERIZAR TABULEIRO
// ============================================================
function renderBoard() {
    const boardDiv = document.getElementById('board');
    boardDiv.innerHTML = '';

    for (let r = 0; r < 8; r++) {
        for (let c = 0; c < 8; c++) {
            const sq = document.createElement('div');
            const isDark = (r + c) % 2 === 1;
            sq.className = `square ${isDark ? 'dark' : 'light'}`;

            const peca = (currentBoard[r] && currentBoard[r][c]) ? currentBoard[r][c] : '.';

            // Destaque de seleção na casa
            if (selectedSquare && selectedSquare.row === r && selectedSquare.col === c) {
                sq.classList.add('selected-sq');
            }

            if (peca !== '.') {
                const p = document.createElement('div');
                p.className = `piece ${peca}`;

                // Anima se a peça mudou de posição
                if (lastBoard && lastBoard[r] && lastBoard[r][c] !== peca) {
                    p.classList.add('just-placed');
                }

                if (selectedSquare && selectedSquare.row === r && selectedSquare.col === c) {
                    p.classList.add('selected-piece');
                }

                sq.appendChild(p);
            }

            sq.onclick = () => handleSquareClick(r, c);
            boardDiv.appendChild(sq);
        }
    }
}

// ============================================================
// CLIQUE NO TABULEIRO
// ============================================================
function handleSquareClick(r, c) {
    if (!meuTurno) return;

    const peca      = currentBoard[r] ? currentBoard[r][c] : '.';
    const minhaCor  = document.getElementById('playerColor').value;

    if (!selectedSquare) {
        if (peca !== '.' && peca.toLowerCase() === minhaCor) {
            selectedSquare = { row: r, col: c };
            renderBoard();
        }
    } else {
        // Desselecionar
        if (selectedSquare.row === r && selectedSquare.col === c) {
            selectedSquare = null;
            renderBoard();
            return;
        }
        // Trocar seleção para outra peça própria
        if (peca !== '.' && peca.toLowerCase() === minhaCor) {
            selectedSquare = { row: r, col: c };
            renderBoard();
            return;
        }
        // Enviar jogada
        ws.send(JSON.stringify({
            type: 'move',
            from: indexToAlgebraic(selectedSquare.row, selectedSquare.col),
            to:   indexToAlgebraic(r, c)
        }));
        selectedSquare = null;
    }
}

function indexToAlgebraic(r, c) {
    return String.fromCharCode(65 + c) + (8 - r).toString();
}

// ============================================================
// CONTAGEM DE PEÇAS CAPTURADAS
// ============================================================
function contarPecas(board) {
    let w = 0, b = 0;
    for (let r = 0; r < 8; r++)
        for (let c = 0; c < 8; c++) {
            const p = board[r] ? board[r][c] : '.';
            if (p === 'w' || p === 'W') w++;
            if (p === 'b' || p === 'B') b++;
        }
    return { w, b };
}

function atualizarCapturados(prevBoard, newBoard) {
    if (!prevBoard) return;
    const prev = contarPecas(prevBoard);
    const curr = contarPecas(newBoard);
    capturedBlack += Math.max(0, prev.b - curr.b);
    capturedWhite += Math.max(0, prev.w - curr.w);
    renderCaptureDots();
}

function renderCaptureDots() {
    const byWhite = document.getElementById('capturedByWhite');
    const byBlack = document.getElementById('capturedByBlack');
    byWhite.innerHTML = '';
    byBlack.innerHTML = '';
    for (let i = 0; i < 12; i++) {
        const dW = document.createElement('div');
        dW.className = `capture-dot ${i < capturedByWhite ? 'black' : 'empty'}`;
        byWhite.appendChild(dW);

        const dB = document.createElement('div');
        dB.className = `capture-dot ${i < capturedByBlack ? 'white' : 'empty'}`;
        byBlack.appendChild(dB);
    }
}

// variáveis de contagem (corrige referências no render)
let capturedByWhite = 0;
let capturedByBlack = 0;

function atualizarCapturados2(prevBoard, newBoard) {
    if (!prevBoard) return;
    const prev = contarPecas(prevBoard);
    const curr = contarPecas(newBoard);
    capturedByWhite += Math.max(0, prev.b - curr.b);
    capturedByBlack += Math.max(0, prev.w - curr.w);
    renderCaptureDots();
}

// ============================================================
// ATUALIZAR STATUS / INDICADOR
// ============================================================
function atualizarStatus(turno, ehMeuTurno, modo) {
    const ind  = document.getElementById('turnIndicator');
    const stat = document.getElementById('status');

    if (modo === 'ia' && turno === 'b') {
        ind.className  = 'turn-indicator thinking';
        stat.textContent = 'IA calculando...';
    } else if (ehMeuTurno) {
        ind.className  = 'turn-indicator ' + (turno === 'w' ? 'white' : 'black');
        stat.textContent = `Seu turno — ${turno === 'w' ? 'Brancas' : 'Pretas'}`;
    } else {
        ind.className  = 'turn-indicator ' + (turno === 'w' ? 'white' : 'black');
        stat.textContent = 'Aguardando adversário...';
    }
}

// ============================================================
// WEBSOCKET HELPERS
// ============================================================
function onMensagemServidor(data, minhaCor) {
    if (data.type === 'init' || data.type === 'update') {
        // Sincroniza regras no game footer
        const sel = document.getElementById('rulesSelectGame');
        if (sel) sel.value = data.regras;

        atualizarCapturados2(lastBoard, data.board);
        lastBoard     = currentBoard.map(r => [...r]);
        currentBoard  = data.board;
        meuTurno      = (data.turn === minhaCor);

        atualizarStatus(data.turn, meuTurno, modoAtual);
        renderBoard();

        if (data.alerta) showToast(data.alerta, 'info');

    } else if (data.type === 'invalid_move' || data.type === 'error') {
        showToast(data.message, 'error');
        selectedSquare = null;
        renderBoard();

    } else if (data.type === 'game_over') {
        abrirModalFimJogo(data.winner);
    }
}

// ============================================================
// JOGAR CONTRA IA
// ============================================================
function jogarContraIA() {
    modoAtual = 'ia';
    capturedByWhite = 0;
    capturedByBlack = 0;
    lastBoard  = null;

    document.getElementById('playerColor').value = 'w';
    if (ws) ws.close();

    const gameIdRandom = 'ia_room_' + Math.floor(Math.random() * 9999);
    ws = new WebSocket(`ws://localhost:6500/ws/ia/${gameIdRandom}`);

    ws.onopen = () => {
        mudarTela('screenGame');
        renderizarCoordenadas();
        renderCaptureDots();
        showToast('Conectado! Boa sorte, jogador.', 'success');
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        onMensagemServidor(data, 'w');
    };

    ws.onerror = () => showToast('Erro de conexão com o servidor.', 'error');
}

// ============================================================
// LOBBY → ARENA
// ============================================================
function irParaLobby(modo) {
    modoAtual = modo;
    const title = document.getElementById('lobbyTitle');

    if (modo === 'online') {
        title.textContent = 'Matchmaking Online';
    } else if (modo === 'apostada') {
        title.textContent = 'Mesa Apostada — Lockstep';
        showToast('Modo apostado: infraestrutura financeira (Pix + PostgreSQL) será ativada ao conectar.', 'info', 5000);
    }
    mudarTela('screenLobby');
}

function conectarServidor() {
    const gameId = document.getElementById('gameId').value.trim();
    const color  = document.getElementById('playerColor').value;
    const rules  = document.getElementById('rulesSelectLobby').value;

    if (!gameId) { showToast('Informe o ID da sala.', 'error'); return; }

    capturedByWhite = 0;
    capturedByBlack = 0;
    lastBoard  = null;

    if (ws) ws.close();
    ws = new WebSocket(`ws://localhost:6500/ws/partida/${gameId}/${color}`);

    ws.onopen = () => {
        mudarTela('screenGame');
        renderizarCoordenadas();
        renderCaptureDots();
        // Enviar regras escolhidas no lobby assim que conectar
        ws.send(JSON.stringify({ type: 'config_rules', regras: rules }));
        showToast('Conectado! Sincronizando com o servidor...', 'success');
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        onMensagemServidor(data, color);
    };

    ws.onerror = () => showToast('Falha ao conectar com o servidor.', 'error');
}

function mudarRegras(novaRegra) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'config_rules', regras: novaRegra }));
    }
}

// ============================================================
// ABANDONO COM CONFIRMAÇÃO
// ============================================================
function confirmarAbandono() {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay open';
    overlay.innerHTML = `
        <div class="modal-box">
            <div class="modal-trophy" style="font-size:40px;">🏳</div>
            <div class="modal-title" style="font-size:1.3rem;">Abandonar?</div>
            <div class="modal-subtitle">Você perderá a partida atual.</div>
            <div class="modal-actions">
                <button class="btn-modal-secondary" onclick="this.closest('.modal-overlay').remove()">Continuar</button>
                <button class="btn-modal-primary"   onclick="abandonarPartida()">Abandonar</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
}

function abandonarPartida() {
    document.querySelectorAll('.modal-overlay').forEach(m => m.remove());
    if (ws) ws.close();
    mudarTela('screenMenu');
}

// ============================================================
// MODAL FIM DE JOGO
// ============================================================
function abrirModalFimJogo(winner) {
    const modal = document.getElementById('modalFimJogo');
    const trophy   = document.getElementById('modalTrophy');
    const title    = document.getElementById('modalTitle');
    const subtitle = document.getElementById('modalSubtitle');

    if (winner === 'w') {
        trophy.textContent   = '🏆';
        title.textContent    = 'Vitória!';
        subtitle.textContent = 'As Brancas venceram a partida.';
    } else if (winner === 'b') {
        trophy.textContent   = modoAtual === 'ia' ? '💀' : '🏆';
        title.textContent    = modoAtual === 'ia' ? 'Derrota' : 'Vitória!';
        subtitle.textContent = modoAtual === 'ia' ? 'A IA venceu desta vez. Tente novamente!' : 'As Pretas venceram a partida.';
    } else {
        trophy.textContent   = '🤝';
        title.textContent    = 'Empate';
        subtitle.textContent = 'A partida terminou sem vencedor.';
    }
    modal.classList.add('open');
}

function fecharModal() {
    document.getElementById('modalFimJogo').classList.remove('open');
}

// ============================================================
// GOOGLE AUTH
// ============================================================
function inicializarBotaoGoogle() {
    if (typeof google !== 'undefined' && google.accounts && google.accounts.id) {
        google.accounts.id.initialize({
            client_id: CONFIG.GOOGLE_CLIENT_ID,
            callback:  handleCredentialResponse
        });
        google.accounts.id.renderButton(
            document.getElementById('googleBtnContainer'),
            { theme: 'filled_black', size: 'medium', type: 'standard', shape: 'pill', width: 200 }
        );
    } else {
        setTimeout(inicializarBotaoGoogle, 100);
    }
}

async function handleCredentialResponse(response) {
    try {
        const res = await fetch('http://localhost:6500/auth/google', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ token: response.credential })
        });

        if (res.ok) {
            const userData = await res.json();
            document.getElementById('googleBtnContainer').style.display = 'none';
            const ui = document.getElementById('userInfo');
            ui.style.display = 'flex';
            document.getElementById('userName').textContent   = userData.name;
            document.getElementById('userPicture').src        = userData.picture;
            showToast(`Bem-vindo, ${userData.name}!`, 'success');
        } else {
            showToast('Erro ao validar credenciais do Google.', 'error');
        }
    } catch (err) {
        showToast('Não foi possível comunicar com o servidor.', 'error');
        console.error(err);
    }
}

function executarLogout() {
    google.accounts.id.disableAutoSelect();
    document.getElementById('googleBtnContainer').style.display = 'block';
    document.getElementById('userInfo').style.display = 'none';
    showToast('Sessão encerrada.', 'info', 2000);
}

// ============================================================
// INICIALIZAÇÃO
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    inicializarBotaoGoogle();
});