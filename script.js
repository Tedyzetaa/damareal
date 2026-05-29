// ============================================================
// ESTADO GLOBAL
// ============================================================
let ws              = null;
let selectedSquare  = null;
let currentBoard    = [];
let meuTurno        = false;
let modoAtual       = '';         // 'ia' | 'online' | 'apostada' | 'searching' | 'private_wait'
let lastBoard       = null;
let capturedByWhite = 0;
let capturedByBlack = 0;
let partidaIdAtual    = null;
let partidaRegistrada = false;
let minhaCorAtual   = 'w';
let opponentPicture = ""; // foto do adversário atual

let pendingPrivateGameId = null;
let pendingPrivateUrl    = null;

let userSaldo = 0;            // saldo em reais (apenas para depósitos PIX)
let userMoedas = 0;           // moedas para apostas e recompensas
let saldoInterval = null;     // polling para atualização automática

// Variáveis para aposta
let apostaSalaId = null;
let apostaPollingInterval = null;
let apostaValorSelecionado = null;

// Valores permitidos e prêmios (valores em moedas)
const VALORES_APOSTA = [1, 3, 5, 10, 20, 50];

let userProfile = {
    googleId:      null,
    nick:          "",
    bio:           "",
    telefone:      "",
    cpf:           "",
    dataNascimento:"",
    privacidade:   { telefone: false, cpf: false },
    patenteOnline: "bronze", patenteApostado: "bronze",
    pontosOnline: 0, pontosApostado: 0
};

let historicoJogos = [];

// API dinâmica conforme ambiente
const API = CONFIG.API_URL;

// ============================================================
// TOAST
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
// HEALTH CHECK
// ============================================================
async function healthCheck() {
    showToast('Conectando ao servidor (pode levar ~30s)...', 'info', 8000);
    try {
        const controller = new AbortController();
        const timeoutId  = setTimeout(() => controller.abort(), 60000);
        const res        = await fetch(`${API}/ping`, { signal: controller.signal });
        clearTimeout(timeoutId);
        if (res.ok) {
            showToast('Servidor pronto!', 'success', 2000);
        } else {
            showToast('Servidor retornou erro. Tente novamente.', 'error');
        }
    } catch (e) {
        showToast('Servidor demorou para responder. Tente novamente.', 'error');
    }
}

function iniciarKeepalive() {
    setInterval(async () => {
        try {
            await fetch(`${API}/ping`);
        } catch (_) { /* silencioso */ }
    }, 10 * 60 * 1000); // a cada 10 minutos
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
    ['A','B','C','D','E','F','G','H'].forEach(l => {
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
            const sq    = document.createElement('div');
            const isDark = (r + c) % 2 === 1;
            sq.className = `square ${isDark ? 'dark' : 'light'}`;
            const peca   = (currentBoard[r] && currentBoard[r][c]) ? currentBoard[r][c] : '.';
            if (selectedSquare && selectedSquare.row === r && selectedSquare.col === c)
                sq.classList.add('selected-sq');
            if (peca !== '.') {
                const p = document.createElement('div');
                p.className = `piece ${peca}`;
                if (lastBoard && lastBoard[r] && lastBoard[r][c] !== peca)
                    p.classList.add('just-placed');
                if (selectedSquare && selectedSquare.row === r && selectedSquare.col === c)
                    p.classList.add('selected-piece');
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
    const peca = currentBoard[r] ? currentBoard[r][c] : '.';
    if (!selectedSquare) {
        if (peca !== '.' && peca.toLowerCase() === minhaCorAtual) {
            selectedSquare = { row: r, col: c };
            renderBoard();
        }
    } else {
        if (selectedSquare.row === r && selectedSquare.col === c) {
            selectedSquare = null; renderBoard(); return;
        }
        if (peca !== '.' && peca.toLowerCase() === minhaCorAtual) {
            selectedSquare = { row: r, col: c }; renderBoard(); return;
        }
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
// CHAT EM TEMPO REAL
// ============================================================
function enviarMensagemChat() {
    const input = document.getElementById('chatInput');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        showToast("Conexão perdida. Não é possível enviar mensagem.", "error");
        return;
    }
    const nick = userProfile.nick || document.getElementById('userName')?.textContent || "Jogador";
    ws.send(JSON.stringify({
        type: "chat",
        nick: nick,
        text: text,
        sender_id: userProfile.googleId
    }));
    input.value = "";
    input.focus();
}

function limparChat() {
    const container = document.getElementById('chatMessages');
    if (container) {
        container.innerHTML = '<div class="chat-empty">Nenhuma mensagem ainda. Diga olá!</div>';
    }
}

function adicionarMensagemChat(remetente, texto, isMe, timestamp) {
    const container = document.getElementById('chatMessages');
    if (!container) return;

    if (!isMe && !opponentPicture) {
        opponentPicture = `https://ui-avatars.com/api/?name=${encodeURIComponent(remetente)}&background=3a2210&color=c8963c&size=64`;
    }
    
    const emptyMsg = container.querySelector('.chat-empty');
    if (emptyMsg) emptyMsg.remove();

    let hora = timestamp || '';
    if (!hora) {
        const agora = new Date();
        hora = agora.getHours().toString().padStart(2, '0') + ':' +
               agora.getMinutes().toString().padStart(2, '0');
    }

    const minhaFoto = userProfile.picture
        || document.getElementById('userPicture')?.src
        || '';

    const fotoOponente = opponentPicture
        || `https://ui-avatars.com/api/?name=${encodeURIComponent(remetente)}&background=3a2210&color=c8963c&size=64`;

    const avatarSrc = isMe ? minhaFoto : fotoOponente;

    const row = document.createElement('div');
    row.className = `chat-row ${isMe ? 'chat-row--me' : 'chat-row--opponent'}`;

    const avatar = document.createElement('img');
    avatar.className = 'chat-avatar';
    avatar.src = avatarSrc;
    avatar.alt = isMe ? 'Eu' : remetente;

    const wrapper = document.createElement('div');
    wrapper.className = `chat-wrapper ${isMe ? 'me' : 'opponent'}`;

    if (!isMe) {
        const nickEl = document.createElement('div');
        nickEl.className = 'chat-nick';
        nickEl.textContent = remetente;
        wrapper.appendChild(nickEl);
    }

    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';
    bubble.textContent = texto; 

    const time = document.createElement('span');
    time.className = 'chat-time';
    time.textContent = hora;

    bubble.appendChild(time);
    wrapper.appendChild(bubble);

    if (isMe) {
        row.appendChild(wrapper);
        row.appendChild(avatar);
    } else {
        row.appendChild(avatar);
        row.appendChild(wrapper);
    }

    container.appendChild(row);
    container.scrollTop = container.scrollHeight;
}

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function toggleChat() {
    const panel = document.getElementById('chatPanel');
    if (!panel) return;
    panel.classList.toggle('chat-panel--collapsed');
    const btn = panel.querySelector('.chat-toggle');
    if (btn) {
        btn.textContent = panel.classList.contains('chat-panel--collapsed') ? '+' : '−';
    }
}

// ============================================================
// PEÇAS CAPTURADAS
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

function atualizarCapturados2(prevBoard, newBoard) {
    if (!prevBoard || !prevBoard.length) return;
    const prev = contarPecas(prevBoard);
    const curr = contarPecas(newBoard);
    capturedByWhite += Math.max(0, prev.b - curr.b);
    capturedByBlack += Math.max(0, prev.w - curr.w);
    renderCaptureDots();
}

function renderCaptureDots() {
    const byWhite = document.getElementById('capturedByWhite');
    const byBlack = document.getElementById('capturedByBlack');
    byWhite.innerHTML = ''; byBlack.innerHTML = '';
    for (let i = 0; i < 12; i++) {
        const dW = document.createElement('div');
        dW.className = `capture-dot ${i < capturedByWhite ? 'black' : 'empty'}`;
        byWhite.appendChild(dW);
        const dB = document.createElement('div');
        dB.className = `capture-dot ${i < capturedByBlack ? 'white' : 'empty'}`;
        byBlack.appendChild(dB);
    }
}

// ============================================================
// STATUS / INDICADOR
// ============================================================
function atualizarStatus(turno, ehMeuTurno, modo) {
    const ind  = document.getElementById('turnIndicator');
    const stat = document.getElementById('status');
    if (modo === 'ia' && !ehMeuTurno) {
        ind.className = 'turn-indicator thinking';
        stat.textContent = 'IA calculando...';
    } else if (ehMeuTurno) {
        ind.className = 'turn-indicator ' + (turno === 'w' ? 'white' : 'black');
        stat.textContent = `Seu turno — ${turno === 'w' ? 'Brancas' : 'Pretas'}`;
    } else {
        ind.className = 'turn-indicator ' + (turno === 'w' ? 'white' : 'black');
        stat.textContent = 'Aguardando adversário...';
    }
}

// ============================================================
// WEBSOCKET — MENSAGENS DO SERVIDOR
// ============================================================
function onMensagemServidor(data, minhaCor) {
    if (data.type === 'init' || data.type === 'update') {
        const sel = document.getElementById('rulesSelectGame');
        if (sel) sel.value = data.regras;

        const oldBoard = currentBoard.length ? currentBoard.map(row => [...row]) : null;
        currentBoard = data.board;
        if (oldBoard) {
            atualizarCapturados2(oldBoard, currentBoard);
        }

        meuTurno = (data.turn === minhaCor);
        renderBoard();
        atualizarStatus(data.turn, meuTurno, modoAtual);

        // Se estávamos em modo private_wait e a partida começou, muda para online e restaura botão
        if (modoAtual === 'private_wait') {
            modoAtual = 'online';
            const btnAbandono = document.getElementById('btn-surrender');
            if (btnAbandono) {
                btnAbandono.textContent = '🏳 Abandonar';
                btnAbandono.onclick = confirmarAbandono;
            }
        }

        if (data.alerta) showToast(data.alerta, 'info');
        if (data.must_continue) {
            showToast('Captura múltipla! Continue movendo a mesma peça.', 'info', 3000);
            selectedSquare = { row: data.piece[0], col: data.piece[1] };
            renderBoard();
        }
    } else if (data.type === 'invalid_move' || data.type === 'error') {
        showToast(data.message, 'error');
        selectedSquare = null;
        renderBoard();
    } else if (data.type === 'game_over') {
        partidaIdAtual    = data.partida_id || null;
        partidaRegistrada = false;
        if (data.board) {
            currentBoard = data.board;
            renderBoard();
        }
        abrirModalFimJogo(data.winner, minhaCor);
    } else if (data.type === 'opponent_left') {
        showToast(data.message || 'O adversário se desconectou.', 'error', 5000);
        meuTurno = false;
    } else if (data.type === 'rematch_offer') {
        if (data.offered_by !== minhaCorAtual) {
            showToast("O oponente ofereceu uma revanche!", "success");
            const btnRepeat = document.getElementById('btnJogarNovamente');
            if (btnRepeat && modoAtual === 'aposta') {
                btnRepeat.textContent = 'Aceitar Revanche';
                btnRepeat.onclick = () => aceitarRevancheAposta();
            }
        }
    } else if (data.type === 'rematch_started') {
        fecharModal();
        const novaCor = data.jogador1_id === userProfile.googleId ? data.cor_jogador1 : data.cor_jogador2;
        conectarPartidaAposta(data.game_id, data.sala_id, data.valor, data.premio, novaCor);
    } else if (data.type === 'chat') {
        const isMe = data.sender_id && userProfile.googleId
            ? data.sender_id === userProfile.googleId
            : data.nick === (userProfile.nick || document.getElementById('userName')?.textContent || '');
        adicionarMensagemChat(data.nick, data.text, isMe, data.timestamp);
    }
}

// ============================================================
// JOGAR CONTRA IA — COR ALEATÓRIA
// ============================================================
function jogarContraIA() {
    const corAleatoria = Math.random() < 0.5 ? 'w' : 'b';
    const corNome      = corAleatoria === 'w' ? 'Brancas' : 'Pretas';

    modoAtual       = 'ia';
    capturedByWhite = 0;
    capturedByBlack = 0;
    lastBoard       = null;
    currentBoard    = [];
    partidaIdAtual  = null;
    partidaRegistrada = false;
    minhaCorAtual   = corAleatoria;

    limparChat();
    if (ws) ws.close();

    const gameId      = 'ia_' + Math.floor(Math.random() * 99999);
    const wsProtocol  = API.startsWith('https') ? 'wss' : 'ws';
    const baseWsUrl   = API.split('://')[1];
    ws = new WebSocket(`${wsProtocol}://${baseWsUrl}/ws/ia/${gameId}/${corAleatoria}`);

    ws.onopen = () => {
        mudarTela('screenGame');
        renderizarCoordenadas();
        renderCaptureDots();
        const btnAbandono = document.getElementById('btn-surrender');
        if (btnAbandono) {
            btnAbandono.textContent = '🏳 Abandonar';
            btnAbandono.onclick = confirmarAbandono;
        }
        const chatInput = document.getElementById('chatInput');
        if (chatInput) {
            chatInput.removeEventListener('keydown', handleChatEnter);
            chatInput.addEventListener('keydown', handleChatEnter);
        }
        showToast(`Você joga de ${corNome}. Boa sorte!`, 'success');
    };
    ws.onmessage = (e) => onMensagemServidor(JSON.parse(e.data), minhaCorAtual);
    ws.onclose = (event) => {
        if (event.code !== 1000) {
            showToast('Conexão com o servidor perdida. Tente novamente.', 'error', 5000);
            mudarTela('screenMenu');
        }
    };
    ws.onerror = () => showToast('Erro de conexão com o servidor.', 'error');
}

// ============================================================
// MATCHMAKING ONLINE
// ============================================================
function iniciarMatchmaking() {
    if (!userProfile.googleId) {
        showToast("Faça login antes de jogar online.", "error");
        return;
    }

    modoAtual = 'searching';

    mudarTela('screenGame');
    currentBoard = [];
    limparChat();
    document.getElementById('status').textContent   = 'Procurando oponente...';
    document.getElementById('turnIndicator').className = 'turn-indicator thinking';

    const btnAbandono = document.getElementById('btn-surrender');
    if (btnAbandono) {
        btnAbandono.textContent = '✕ Cancelar Busca';
        btnAbandono.onclick = cancelarMatchmaking;
    }

    const wsProtocol = API.startsWith('https') ? 'wss' : 'ws';
    const lobbyWs = new WebSocket(`${wsProtocol}://${API.split('://')[1]}/ws/lobby?google_id=${userProfile.googleId}`);
    window.lobbySocket = lobbyWs;

    lobbyWs.onopen = () => {
        showToast("Conectado à fila. Aguardando adversário...", "info");
    };
    lobbyWs.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === "matched") {
            opponentPicture = data.opponent_picture || "";
            const corNome = data.color === 'w' ? 'Brancas' : 'Pretas';
            showToast(`Oponente encontrado: ${data.opponent_nick || 'Jogador'}! Você joga de ${corNome}.`, "success", 4000);
            window.lobbySocket = null;
            conectarPartidaOnline(data.game_id, data.color);
        }
    };
    lobbyWs.onclose = (event) => {
        if (event.code !== 1000 && modoAtual === 'searching') {
            showToast("Conexão com a fila perdida. Tente novamente.", "error");
            mudarTela('screenMenu');
        }
    };
    lobbyWs.onerror = () => {
        if (modoAtual === 'searching') {
            showToast("Erro ao entrar na fila.", "error");
            mudarTela('screenMenu');
        }
    };
}

function cancelarMatchmaking() {
    if (window.lobbySocket && window.lobbySocket.readyState === WebSocket.OPEN) {
        window.lobbySocket.send(JSON.stringify({ type: "cancel" }));
        window.lobbySocket.close(1000);
    }
    window.lobbySocket = null;
    modoAtual = '';
    showToast("Busca cancelada.", "info", 2000);
    mudarTela('screenMenu');
}

function conectarPartidaOnline(gameId, minhaCor) {
    capturedByWhite   = 0;
    capturedByBlack   = 0;
    lastBoard         = null;
    currentBoard      = [];
    partidaIdAtual    = null;
    partidaRegistrada = false;
    minhaCorAtual     = minhaCor;
    if (modoAtual !== 'private_wait') {
        modoAtual = 'online';
    }
    limparChat();

    if (ws) ws.close();

    const wsProtocol = API.startsWith('https') ? 'wss' : 'ws';
    ws = new WebSocket(`${wsProtocol}://${API.split('://')[1]}/ws/partida/${gameId}/${minhaCor}`);

    ws.onopen = () => {
        mudarTela('screenGame');
        renderizarCoordenadas();
        renderCaptureDots();
        const btnAbandono = document.getElementById('btn-surrender');
        if (btnAbandono) {
            if (modoAtual === 'private_wait') {
                btnAbandono.textContent = 'Cancelar';
                btnAbandono.onclick = () => {
                    if (ws) ws.close(1000, 'Cancelado');
                    modoAtual = '';
                    mudarTela('screenMenu');
                };
                document.getElementById('status').textContent = 'Aguardando amigo entrar...';
            } else {
                btnAbandono.textContent = '🏳 Abandonar';
                btnAbandono.onclick = confirmarAbandono;
            }
        }

        const chatInput = document.getElementById('chatInput');
        if (chatInput) {
            chatInput.removeEventListener('keydown', handleChatEnter);
            chatInput.addEventListener('keydown', handleChatEnter);
        }

        showToast("Conectado! Boa sorte.", "success");
    };
    ws.onmessage = (e) => onMensagemServidor(JSON.parse(e.data), minhaCor);
    ws.onclose = (event) => {
        if (event.code !== 1000) {
            showToast("Conexão com a partida perdida.", "error");
            mudarTela('screenMenu');
        } else if (modoAtual === 'private_wait') {
            modoAtual = '';
            mudarTela('screenMenu');
        }
    };
    ws.onerror = () => showToast("Falha ao conectar à partida.", "error");
}

// ============================================================
// PARTIDA PRIVADA COM LINK
// ============================================================
function gerarLinkSalaPrivada() {
    if (!userProfile.googleId) {
        showToast("Faça login antes de criar uma sala privada.", "error");
        return;
    }
    // Gera um ID único (ex: priv_abc123)
    const gameId = 'priv_' + Math.random().toString(36).substring(2, 10);
    const inviteUrl = `${window.location.origin}${window.location.pathname}?invite=${gameId}`;
    
    // Armazena para uso posterior
    pendingPrivateGameId = gameId;
    pendingPrivateUrl    = inviteUrl;
    
    // Abre o modal de compartilhamento
    abrirModalCompartilhar(inviteUrl);
}

function abrirModalCompartilhar(url) {
    const modal = document.getElementById('modalCompartilhar');
    if (!modal) return;
    
    // Preenche o campo de link
    const input = document.getElementById('inviteLinkInput');
    if (input) input.value = url;
    
    // Exibe o modal
    modal.style.display = 'flex';
}

function fecharModalCompartilhar(iniciarPartida = false) {
    const modal = document.getElementById('modalCompartilhar');
    if (modal) modal.style.display = 'none';
    
    if (iniciarPartida && pendingPrivateGameId) {
        // Inicia a partida privada (aguardando amigo)
        iniciarPartidaPrivada(pendingPrivateGameId);
    }
    
    // Limpa os dados pendentes (opcional)
    if (!iniciarPartida) {
        pendingPrivateGameId = null;
        pendingPrivateUrl    = null;
    }
}

function iniciarPartidaPrivada(gameId) {
    modoAtual = 'private_wait';
    conectarPartidaOnline(gameId, 'w');
}

// ============================================================
// CONFIGURAÇÃO DOS EVENTOS DO MODAL
// ============================================================
function inicializarModalCompartilhar() {
    const modal = document.getElementById('modalCompartilhar');
    if (!modal) return;
    
    // Botão copiar
    const copyBtn = document.getElementById('copyLinkBtn');
    if (copyBtn) {
        copyBtn.onclick = () => {
            const input = document.getElementById('inviteLinkInput');
            if (input && input.value) {
                navigator.clipboard.writeText(input.value).then(() => {
                    showToast("Link copiado com sucesso!", "success", 2000);
                }).catch(() => {
                    showToast("Não foi possível copiar o link.", "error");
                });
            }
        };
    }
    
    // Botões de redes sociais
    const shareButtons = modal.querySelectorAll('.share-btn');
    shareButtons.forEach(btn => {
        btn.onclick = () => {
            const link = document.getElementById('inviteLinkInput').value;
            if (!link) return;
            const platform = btn.getAttribute('data-share');
            let shareUrl = '';
            const text = encodeURIComponent('Jogue Damas comigo! Link: ');
            switch(platform) {
                case 'whatsapp':
                    shareUrl = `https://api.whatsapp.com/send?text=${text}${encodeURIComponent(link)}`;
                    break;
                case 'facebook':
                    shareUrl = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(link)}`;
                    break;
                case 'telegram':
                    shareUrl = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent('Jogue Damas comigo!')}`;
                    break;
                case 'twitter':
                    shareUrl = `https://twitter.com/intent/tweet?text=${encodeURIComponent('Jogue Damas comigo! ' + link)}`;
                    break;
                default: return;
            }
            window.open(shareUrl, '_blank', 'noopener,noreferrer');
        };
    });
    
    // Botão Cancelar
    const cancelarBtn = document.getElementById('cancelarCompartilharBtn');
    if (cancelarBtn) {
        cancelarBtn.onclick = () => fecharModalCompartilhar(false);
    }
    
    // Botão Iniciar Partida
    const iniciarBtn = document.getElementById('iniciarPartidaBtn');
    if (iniciarBtn) {
        iniciarBtn.onclick = () => fecharModalCompartilhar(true);
    }
}

// ============================================================
// INTERCEPTAÇÃO DE CONVITE NA URL
// ============================================================
function checkForInvite() {
    const urlParams = new URLSearchParams(window.location.search);
    const inviteId = urlParams.get('invite');
    if (!inviteId) return;

    // Remove o parâmetro da URL (sem recarregar a página)
    const newUrl = window.location.origin + window.location.pathname;
    window.history.replaceState({}, document.title, newUrl);

    // Cria modal de convite se não existir
    let modal = document.getElementById('modalInvite');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'modalInvite';
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal-box">
                <div class="modal-trophy">🔗</div>
                <div class="modal-title">Convite para Partida</div>
                <div class="modal-subtitle" id="inviteMessage">Você foi convidado para uma partida privada!</div>
                <div class="modal-actions">
                    <button class="btn-modal-secondary" id="btnInviteCancel">Cancelar</button>
                    <button class="btn-modal-primary" id="btnInviteAccept">Entrar na Sala</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        document.getElementById('btnInviteCancel').onclick = () => {
            modal.style.display = 'none';
            mudarTela('screenMenu');
        };
        document.getElementById('btnInviteAccept').onclick = () => {
            modal.style.display = 'none';
            if (userProfile.googleId) {
                conectarPartidaOnline(inviteId, 'b');
            } else {
                showToast("Você precisa fazer login para aceitar o convite.", "error", 5000);
                mudarTela('screenMenu');
            }
        };
    }
    modal.style.display = 'flex';
}

// ============================================================
// LOBBY MANUAL (SALA APOSTADA / TESTE)
// ============================================================
function irParaLobby(modo) {
    modoAtual = modo;
    document.getElementById('lobbyTitle').textContent =
        modo === 'online' ? 'Matchmaking Online' : 'Mesa Apostada — Lockstep';
    if (modo === 'apostada')
        showToast('Modo apostado: infraestrutura financeira será ativada ao conectar.', 'info', 5000);
    mudarTela('screenLobby');
}

function conectarServidor() {
    const gameId = document.getElementById('gameId').value.trim();
    const color  = document.getElementById('playerColor').value;
    const rules  = document.getElementById('rulesSelectLobby').value;
    if (!gameId) { showToast('Informe o ID da sala.', 'error'); return; }

    capturedByWhite   = 0;
    capturedByBlack   = 0;
    lastBoard         = null;
    currentBoard      = [];
    partidaIdAtual    = null;
    partidaRegistrada = false;
    minhaCorAtual     = color;
    opponentPicture   = '';

    limparChat();
    if (ws) ws.close();
    const wsProtocol = API.startsWith('https') ? 'wss' : 'ws';
    ws = new WebSocket(`${wsProtocol}://${API.split('://')[1]}/ws/partida/${gameId}/${color}`);

    ws.onopen = () => {
        mudarTela('screenGame');
        renderizarCoordenadas();
        renderCaptureDots();
        const btnAbandono = document.getElementById('btn-surrender');
        if (btnAbandono) {
            btnAbandono.textContent = '🏳 Abandonar';
            btnAbandono.onclick = confirmarAbandono;
        }

        const chatInput = document.getElementById('chatInput');
        if (chatInput) {
            chatInput.removeEventListener('keydown', handleChatEnter);
            chatInput.addEventListener('keydown', handleChatEnter);
        }

        ws.send(JSON.stringify({ type: 'config_rules', regras: rules }));
        showToast('Conectado!', 'success');
    };
    ws.onmessage = (e) => onMensagemServidor(JSON.parse(e.data), color);
    ws.onclose   = (event) => {
        if (event.code !== 1000) {
            showToast('Conexão com o servidor perdida. Tente novamente.', 'error', 5000);
            mudarTela('screenMenu');
        }
    };
    ws.onerror = () => showToast('Falha ao conectar com o servidor.', 'error');
}

function handleChatEnter(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        enviarMensagemChat();
    }
}

function mudarRegras(novaRegra) {
    if (ws && ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: 'config_rules', regras: novaRegra }));
}

// ============================================================
// MOEDAS E BÔNUS DIÁRIO
// ============================================================
function atualizarMoedasUI(moedas) {
    userMoedas = moedas;
    const moedaSpan = document.getElementById('userMoedasDisplay');
    const apostaMoedasSpan = document.getElementById('apostaMoedasDisplay');
    if (moedaSpan) moedaSpan.textContent = `${moedas} moedas`;
    if (apostaMoedasSpan) apostaMoedasSpan.textContent = moedas;
    const saldoBox = document.getElementById('saldoContainer');
    if (saldoBox) saldoBox.style.display = 'flex';
}

async function verificarEExibirBonusDiario() {
    if (!userProfile.googleId) return;
    try {
        const res = await fetch(`${API}/api/bonus/disponivel?google_id=${userProfile.googleId}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.disponivel) {
            exibirModalBonusDiario();
        }
    } catch (e) { console.warn('Erro ao verificar bônus diário:', e); }
}

function exibirModalBonusDiario() {
    let modal = document.getElementById('modalBonusDiario');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'modalBonusDiario';
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal-box">
                <div class="modal-trophy">🎁</div>
                <div class="modal-title">Bônus Diário!</div>
                <div class="modal-subtitle">Você tem 300 moedas esperando por você. Resgate agora!</div>
                <div class="modal-actions">
                    <button class="btn-modal-secondary" onclick="fecharModalBonus()">Agora não</button>
                    <button class="btn-modal-primary" id="btnResgatarBonus">Resgatar Bônus</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        document.getElementById('btnResgatarBonus').onclick = async () => {
            fecharModalBonus();
            await resgatarBonusDiario();
        };
    }
    modal.style.display = 'flex';
}

function fecharModalBonus() {
    const modal = document.getElementById('modalBonusDiario');
    if (modal) modal.style.display = 'none';
}

async function resgatarBonusDiario() {
    const credential = localStorage.getItem('dr_credential');
    if (!credential || !userProfile.googleId) return;
    try {
        const res = await fetch(`${API}/api/bonus/resgatar`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: credential })
        });
        const data = await res.json();
        if (res.ok && data.resgatado) {
            userMoedas = data.moedas;
            atualizarMoedasUI(userMoedas);
            showToast('🎉 Bônus resgatado! +300 moedas!', 'success', 4000);
        } else {
            showToast('Erro ao resgatar bônus.', 'error');
        }
    } catch (e) {
        showToast('Erro de conexão.', 'error');
    }
}

// ============================================================
// RANKING
// ============================================================
let rankingModoAtual = 'online';
const PATENTE_ICONE = { bronze: '🥉', prata: '🥈', ouro: '🥇', mestre: '👑' };

async function carregarRanking(modo = 'online') {
    rankingModoAtual = modo;
    document.getElementById('tabRankingOnline').className = modo === 'online' ? 'btn-modal-primary' : 'btn-modal-secondary';
    document.getElementById('tabRankingApostado').className = modo === 'apostado' ? 'btn-modal-primary' : 'btn-modal-secondary';
    const lista = document.getElementById('rankingLista');
    lista.innerHTML = '<div class="chat-empty">Carregando...</div>';
    try {
        const res = await fetch(`${API}/api/ranking/${modo}`);
        const data = await res.json();
        renderRanking(data.ranking, modo);
    } catch (e) {
        lista.innerHTML = '<div class="chat-empty">Erro ao carregar ranking.</div>';
    }
}

function renderRanking(lista, modo) {
    const container = document.getElementById('rankingLista');
    if (!lista || lista.length === 0) {
        container.innerHTML = '<div class="chat-empty">Nenhum jogador no ranking ainda.</div>';
        return;
    }
    container.innerHTML = '';
    lista.forEach((jogador) => {
        const isEu = jogador.google_id === userProfile.googleId;
        const medalha = jogador.posicao === 1 ? '🥇' : jogador.posicao === 2 ? '🥈' : jogador.posicao === 3 ? '🥉' : `#${jogador.posicao}`;
        const card = document.createElement('div');
        card.className = 'ranking-card' + (isEu ? ' ranking-card-eu' : '');
        card.innerHTML = `
            <span class="ranking-pos">${medalha}</span>
            <img src="${jogador.foto_url || 'https://ui-avatars.com/api/?name=' + encodeURIComponent(jogador.nome) + '&background=random'}" class="ranking-avatar" onerror="this.src='https://ui-avatars.com/api/?name=?&background=random'">
            <div class="ranking-info">
                <span class="ranking-nome">${escapeHtml(jogador.nome)}</span>
                <span class="ranking-patente">${PATENTE_ICONE[jogador.patente] || '🥉'} ${jogador.patente.charAt(0).toUpperCase() + jogador.patente.slice(1)}</span>
            </div>
            <span class="ranking-pontos">${jogador.pontos} pts</span>
        `;
        if (!isEu && userProfile.googleId) {
            const btnConvite = document.createElement('button');
            btnConvite.className = 'btn-back';
            btnConvite.style.cssText = 'flex-shrink:0; font-size:11px; padding:4px 8px;';
            btnConvite.textContent = '✉ Convidar';
            btnConvite.onclick = () => enviarConvitePersonalizado(jogador.google_id, jogador.nome);
            card.appendChild(btnConvite);
        }
        container.appendChild(card);
    });
    const minhaPos = document.getElementById('rankingMinhaPos');
    if (userProfile.googleId) {
        const eu = lista.find(j => j.google_id === userProfile.googleId);
        if (eu) minhaPos.textContent = `Sua posição: #${eu.posicao} — ${eu.pontos} pts`;
        else minhaPos.textContent = `Você não está no top 50 ainda. Continue jogando!`;
    }
}

// ============================================================
// CONVITE PERSONALIZADO (via WebSocket)
// ============================================================
let lobbyWsConvite = null;

function conectarLobbyParaConvites() {
    if (lobbyWsConvite && lobbyWsConvite.readyState === WebSocket.OPEN) return;
    if (!userProfile.googleId) return;
    const wsProtocol = API.startsWith('https') ? 'wss' : 'ws';
    lobbyWsConvite = new WebSocket(`${wsProtocol}://${API.split('://')[1]}/ws/lobby?google_id=${userProfile.googleId}`);
    lobbyWsConvite.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === 'invite_received') {
            mostrarModalConviteRecebido(data);
        } else if (data.type === 'invite_rejected') {
            showToast('❌ O jogador recusou o convite.', 'error');
        } else if (data.type === 'invite_error') {
            showToast(data.mensagem || 'Jogador não disponível.', 'error');
        } else if (data.type === 'match_found') {
            if (lobbyWsConvite) { lobbyWsConvite.close(); lobbyWsConvite = null; }
            conectarPartidaOnline(data.game_id, data.color);
        }
    };
}

function enviarConvitePersonalizado(toGoogleId, toNick) {
    if (!userProfile.googleId) {
        showToast('Você precisa estar logado para convidar.', 'error');
        return;
    }
    conectarLobbyParaConvites();
    setTimeout(() => {
        if (lobbyWsConvite && lobbyWsConvite.readyState === WebSocket.OPEN) {
            lobbyWsConvite.send(JSON.stringify({
                type: 'invite_send', to_google_id: toGoogleId, from_nick: userProfile.nick || 'Jogador'
            }));
            showToast(`✉ Convite enviado para ${toNick}!`, 'info');
        } else {
            showToast('Conectando ao lobby... tente novamente em instantes.', 'info');
        }
    }, 600);
}

function mostrarModalConviteRecebido(data) {
    let modal = document.getElementById('modalConvite');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'modalConvite';
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal-box">
                <div class="modal-trophy">✉</div>
                <div class="modal-title">Convite Recebido</div>
                <div class="modal-subtitle">${data.from_nick} quer jogar com você!</div>
                <div class="modal-actions">
                    <button class="btn-modal-secondary" id="btnRecusarConvite">Recusar</button>
                    <button class="btn-modal-primary" id="btnAceitarConvite">Aceitar</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    modal.style.display = 'flex';
    document.getElementById('btnAceitarConvite').onclick = () => {
        modal.style.display = 'none';
        if (lobbyWsConvite && lobbyWsConvite.readyState === WebSocket.OPEN) {
            lobbyWsConvite.send(JSON.stringify({ type: 'invite_accept', from_google_id: data.from_google_id }));
        }
    };
    document.getElementById('btnRecusarConvite').onclick = () => {
        modal.style.display = 'none';
        if (lobbyWsConvite && lobbyWsConvite.readyState === WebSocket.OPEN) {
            lobbyWsConvite.send(JSON.stringify({ type: 'invite_reject', from_google_id: data.from_google_id }));
        }
    };
}

// ============================================================
// ABANDONO / CANCELAMENTO
// ============================================================
function confirmarAbandono() {
    if (modoAtual === 'searching') {
        cancelarMatchmaking();
        return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay open';
    const msgFinanceira = modoAtual === 'aposta' && window.apostaInfo
        ? `\n\n⚠️ Você perderá R$ ${window.apostaInfo.valor.toFixed(2)} da aposta!` : '';
    overlay.innerHTML = `
        <div class="modal-box">
            <div class="modal-trophy" style="font-size:40px;">🏳</div>
            <div class="modal-title" style="font-size:1.3rem;">Abandonar?</div>
            <div class="modal-subtitle">Você perderá a partida atual.</div>
            <div class="modal-actions">
                <button class="btn-modal-secondary" onclick="this.closest('.modal-overlay').remove()">Continuar</button>
                <button class="btn-modal-primary"   onclick="abandonarPartida()">${msgFinanceira ? 'Perder Aposta' : 'Abandonar'}</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
}

function abandonarPartida() {
    document.querySelectorAll('.modal-overlay').forEach(m => m.remove());
    if (ws) ws.close(1000, 'Abandono voluntário');
    if (modoAtual === 'private_wait') {
        modoAtual = '';
    }
    mudarTela('screenMenu');
}

// ============================================================
// MODAL FIM DE JOGO — DINÂMICO
// ============================================================
function abrirModalFimJogo(winner, minhaCor) {
    const trophy   = document.getElementById('modalTrophy');
    const title    = document.getElementById('modalTitle');
    const subtitle = document.getElementById('modalSubtitle');
    const btnRepeat = document.getElementById('btnJogarNovamente');
    let resultado;
    let resultadoFinanceiro = '';

    if (modoAtual === 'aposta' && window.apostaInfo) {
        const { valor, premio } = window.apostaInfo;
        if (winner === minhaCor) {
            resultadoFinanceiro = `<br>💰 +R$ ${premio.toFixed(2)} creditado no seu saldo`;
        } else if (winner === 'empate') {
            resultadoFinanceiro = `<br>🔄 Aposta devolvida — R$ ${valor.toFixed(2)}`;
        } else {
            resultadoFinanceiro = `<br>💸 -R$ ${valor.toFixed(2)} debitado do seu saldo`;
        }
    }

    if (winner === minhaCor) {
        trophy.textContent   = '🏆';
        title.textContent    = 'Vitória!';
        subtitle.innerHTML = modoAtual === 'ia' ? 'Você venceu a IA!' : `As ${winner === 'w' ? 'Brancas' : 'Pretas'} venceram.${resultadoFinanceiro}`;
        resultado = 'vitoria';
    } else if (winner === 'empate') {
        trophy.textContent   = '🤝';
        title.textContent    = 'Empate';
        subtitle.innerHTML = `A partida terminou sem vencedor.${resultadoFinanceiro}`;
        resultado = 'empate';
    } else {
        trophy.textContent   = modoAtual === 'ia' ? '💀' : '🏆';
        title.textContent    = modoAtual === 'ia' ? 'Derrota' : 'Vitória do Adversário';
        subtitle.innerHTML = modoAtual === 'ia' ? 'A IA venceu desta vez.' : `As ${winner === 'w' ? 'Brancas' : 'Pretas'} venceram.${resultadoFinanceiro}`;
        resultado = 'derrota';
    }

    if (btnRepeat) {
        if (modoAtual === 'online') {
            btnRepeat.onclick = () => { fecharModal(); iniciarMatchmaking(); };
            btnRepeat.textContent = 'Revanche Online';
        } else if (modoAtual === 'ia') {
            btnRepeat.onclick = () => { fecharModal(); jogarContraIA(); };
            btnRepeat.textContent = 'Jogar Novamente';
        } else if (modoAtual === 'aposta') {
            btnRepeat.onclick = () => oferecerRevancheAposta();
            btnRepeat.textContent = 'Pedir Revanche';
        } else {
            btnRepeat.onclick = () => { fecharModal(); mudarTela('screenMenu'); };
            btnRepeat.textContent = 'Menu';
        }
    }

    document.getElementById('modalFimJogo').classList.add('open');
    if (modoAtual === 'aposta' && window.apostaInfo) {
        registrarJogoNoServidor(resultado, window.apostaInfo.valor);
        setTimeout(() => buscarSaldoAtualizado(), 1500);
    } else {
        registrarJogoNoServidor(resultado, 0);
    }
}

function fecharModal() {
    document.getElementById('modalFimJogo').classList.remove('open');
}

// ============================================================
// REGISTRAR JOGO NO BACKEND
// ============================================================
async function registrarJogoNoServidor(resultado, valorAposta = 0) {
    if (!userProfile.googleId) return;
    if (partidaRegistrada) return;
    partidaRegistrada = true;

    const credential = localStorage.getItem('dr_credential');
    let tipoEnvio = modoAtual || 'online';
    if (tipoEnvio === 'apostada')  tipoEnvio = 'aposta';
    if (tipoEnvio === 'searching') tipoEnvio = 'online';
    if (tipoEnvio === 'private_wait') tipoEnvio = 'online';

    const payload = {
        googleId:   userProfile.googleId,
        tipo:       tipoEnvio,
        resultado,
        valor:      valorAposta,
        partida_id: partidaIdAtual
    };
    try {
        const res = await fetch(`${API}/registrar-jogo`, {
            method: 'POST', 
            headers: { 
                'Content-Type': 'application/json',
                ...(credential ? { 'Authorization': `Bearer ${credential}` } : {})
            },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            const data = await res.json();
            if (data.status === 'registered') {
                showToast(`Partida registrada! Saldo: R$ ${data.novo_saldo.toFixed(2)}`, 'success');
                historicoJogos.unshift({ tipo: payload.tipo, resultado, valor: valorAposta, data: new Date().toLocaleDateString() });
                renderHistorico();
            }
        } else {
            const err = await res.json();
            showToast(`Erro ao registrar: ${err.detail}`, 'error');
            partidaRegistrada = false;
        }
    } catch (e) {
        showToast('Não foi possível registrar a partida.', 'error');
        partidaRegistrada = false;
    }
}

function atualizarSaldoUI(saldo) {
    userSaldo = saldo;
    const saldoSpan = document.getElementById('userSaldo');
    const saldoBox = document.getElementById('saldoContainer');
    if (saldoSpan) {
        saldoSpan.textContent = `R$ ${saldo.toFixed(2)}`;
    }
    if (saldoBox) {
        saldoBox.style.display = 'flex';
    }
}

async function buscarSaldoAtualizado() {
    if (!userProfile.googleId) return;
    try {
        const res = await fetch(`${API}/api/finance/saldo/${userProfile.googleId}`);
        if (res.ok) {
            const data = await res.json();
            if (data.saldo !== undefined) {
                if (data.saldo !== userSaldo) {
                    atualizarSaldoUI(data.saldo);
                    showToast(`Saldo atualizado: R$ ${data.saldo.toFixed(2)}`, 'success', 3000);
                }
            }
        }
    } catch (e) {
        console.warn('Erro ao buscar saldo:', e);
    }
}

function iniciarPollingSaldo() {
    if (saldoInterval) clearInterval(saldoInterval);
    saldoInterval = setInterval(() => {
        if (userProfile.googleId) {
            buscarSaldoAtualizado();
        }
    }, 30000); 
}

// ============================================================
// COMPRA DE MOEDAS (antes Depósito)
// ============================================================
async function solicitarCompraMoedas() {
    const modal = document.getElementById('modalDepositoValor');
    const input = document.getElementById('depositoValorInput');
    input.value = '';
    modal.style.display = 'flex';
    const infoDiv = document.getElementById('infoConversao');
    if (!infoDiv) {
        const info = document.createElement('div');
        info.id = 'infoConversao';
        info.style.marginTop = '10px';
        info.style.fontSize = '12px';
        info.style.color = 'var(--text-secondary)';
        info.innerHTML = '💰 R$ 10,00 = 10.000 moedas';
        modal.querySelector('.modal-box').appendChild(info);
    } else {
        infoDiv.style.display = 'block';
    }
}

function fecharModalDepositoValor() {
    document.getElementById('modalDepositoValor').style.display = 'none';
    const info = document.getElementById('infoConversao');
    if (info) info.style.display = 'none';
}

async function confirmarDeposito() {
    const credential = localStorage.getItem('dr_credential');
    if (!credential) {
        showToast("Você precisa estar logado para depositar.", "error");
        return;
    }

    const input = document.getElementById('depositoValorInput');
    let valor = parseFloat(input.value);
    if (isNaN(valor) || valor < 1) {
        showToast("Valor inválido (mínimo R$1,00)", "error");
        return;
    }
    fecharModalDepositoValor();

    try {
        const res = await fetch(`${API}/api/finance/gerar-pix`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...(credential ? { "Authorization": `Bearer ${credential}` } : {})
            },
            body: JSON.stringify({ googleId: userProfile.googleId, valor, tipo: "compra_moedas" })
        });
        const data = await res.json();
        if (res.ok) {
            const modal = document.getElementById("modalDeposito");
            const qrImg = document.getElementById("depositoQR");
            const qrText = document.getElementById("depositoCodigo");
            qrImg.src = `data:image/png;base64,${data.qr_code}`;
            qrText.value = data.qr_text;
            modal.style.display = "flex";
        } else {
            showToast(data.detail || "Erro ao gerar Pix", "error");
        }
    } catch (e) {
        showToast("Erro de conexão", "error");
    }
}

function fecharModalDeposito() {
    document.getElementById("modalDeposito").style.display = "none";
}

// ============================================================
// GOOGLE AUTH
// ============================================================
function inicializarBotaoGoogle() {
    if (typeof google === 'undefined' || !google.accounts?.id) {
        setTimeout(inicializarBotaoGoogle, 100);
        return;
    }
    google.accounts.id.initialize({
        client_id:             CONFIG.GOOGLE_CLIENT_ID,
        callback:              handleCredentialResponse,
        auto_select:           false,
        cancel_on_tap_outside: false,
        use_fedcm_for_prompt:  true,
    });
    google.accounts.id.renderButton(
        document.getElementById('googleBtnContainer'),
        { theme: 'filled_black', size: 'large', type: 'standard', shape: 'pill', width: 220 }
    );
}

async function handleCredentialResponse(response) {
    localStorage.setItem('dr_credential', response.credential);
    await processarLogin(response.credential);
}

async function processarLogin(token) {
    try {
        const res = await fetch(`${API}/auth/google`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ token })
        });

        if (!res.ok) {
            localStorage.removeItem('dr_credential');
            document.getElementById('googleBtnContainer').style.display = 'block';
            document.getElementById('userInfo').style.display = 'none';
            return;
        }

        const userData = await res.json();
        userProfile.googleId = userData.google_id;
        userProfile.picture  = userData.picture;

        userSaldo = userData.saldo || 0;
        userMoedas = userData.moedas || 0;
        atualizarSaldoUI(userSaldo);
        atualizarMoedasUI(userMoedas);
        if (userData.bonus_cadastro) {
            setTimeout(() => showToast('🎁 Bônus de cadastro: +1000 moedas creditadas!', 'success', 6000), 1500);
        }
        document.getElementById('googleBtnContainer').style.display = 'none';
        const ui = document.getElementById('userInfo');
        ui.style.display = 'flex';

        const nomeExibido = userData.nick || userData.name;
        document.getElementById('userName').textContent = nomeExibido;
        document.getElementById('userPicture').src      = userData.picture;

        showToast(`Bem-vindo, ${nomeExibido}!`, 'success');
        await carregarPerfilDoServidor(userData.google_id);
        iniciarPollingSaldo();
        verificarEExibirBonusDiario();
        conectarLobbyParaConvites();
    } catch (err) {
        console.error("Erro no processamento do login:", err);
        document.getElementById('googleBtnContainer').style.display = 'block';
        document.getElementById('userInfo').style.display = 'none';
    }
}

// ============================================================
// CARREGAR PERFIL DO SERVIDOR
// ============================================================
async function carregarPerfilDoServidor(googleId) {
    if (!googleId) return;
    try {
        const res = await fetch(`${API}/get-profile/${googleId}`);
        if (!res.ok) return;
        const data = await res.json();
        const campos = { nick: 'nick', bio: 'bio', telefone: 'telefone', cpf: 'cpf', data_nasc: 'dataNascimento' };
        Object.entries(campos).forEach(([chave, id]) => {
            const el = document.getElementById(id);
            if (el && data[chave]) el.value = data[chave];
        });
        userProfile.nick           = data.nick          || '';
        userProfile.bio            = data.bio           || '';
        userProfile.telefone       = data.telefone      || '';
        userProfile.cpf            = data.cpf           || '';
        userProfile.dataNascimento = data.data_nasc     || '';
        if (data.nick) {
            document.getElementById('userName').textContent = data.nick;
        }
        if (data.foto_url) {
            document.getElementById('previewFoto').src = data.foto_url;
            document.getElementById('userPicture').src = data.foto_url;
            userProfile.picture = data.foto_url;
        }
        userMoedas = data.moedas || 0;
        atualizarMoedasUI(userMoedas);
        const patenteIcones = { bronze: '🥉', prata: '🥈', ouro: '🥇', mestre: '👑' };
        const patenteNomes = { bronze: 'Bronze', prata: 'Prata', ouro: 'Ouro', mestre: 'Mestre' };
        const pOnline = data.patente_online || 'bronze';
        const pApostado = data.patente_apostado || 'bronze';
        document.getElementById('iconePatenteOnline').textContent = patenteIcones[pOnline];
        document.getElementById('nomePatenteOnline').textContent = patenteNomes[pOnline];
        document.getElementById('pontosPatenteOnline').textContent = `${data.pontos_online || 0} pts`;
        document.getElementById('iconePatenteApostado').textContent = patenteIcones[pApostado];
        document.getElementById('nomePatenteApostado').textContent = patenteNomes[pApostado];
        document.getElementById('pontosPatenteApostado').textContent = `${data.pontos_apostado || 0} pts`;
        userProfile.patenteOnline = pOnline;
        userProfile.patenteApostado = pApostado;
        userProfile.pontosOnline = data.pontos_online || 0;
        userProfile.pontosApostado = data.pontos_apostado || 0;
        historicoJogos = (data.historico || []).map(h => ({
            tipo:      h.tipo,
            resultado: h.resultado,
            valor:     h.valor,
            data:      new Date(h.created_at || h.data).toLocaleDateString('pt-BR')
        }));
        renderHistorico();
    } catch (e) {
        console.warn('Não foi possível carregar o perfil:', e);
    }
}

function executarLogout() {
    google.accounts.id.disableAutoSelect();
    localStorage.removeItem('dr_credential');
    userProfile = {
        googleId: null, nick: "", bio: "", telefone: "", cpf: "", dataNascimento: "",
        privacidade: { telefone: false, cpf: false }, patenteOnline: "bronze", patenteApostado: "bronze", pontosOnline: 0, pontosApostado: 0
    };
    historicoJogos = [];
    ['nick','bio','telefone','cpf','dataNascimento'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    document.getElementById('inputFoto').value   = '';
    document.getElementById('previewFoto').src   = 'https://ui-avatars.com/api/?name=User&background=random';
    document.getElementById('googleBtnContainer').style.display = 'block';
    document.getElementById('userInfo').style.display           = 'none';
    renderHistorico();
    showToast('Sessão encerrada.', 'info', 2000);
}

// ============================================================
// LÓGICA DE PERFIL
// ============================================================
function validarCPF(cpf) {
    cpf = cpf.replace(/[^\d]+/g, '');
    if (cpf.length !== 11 || /^(\d)\1{10}$/.test(cpf)) return false;
    let soma = 0, resto;
    for (let i = 1; i <= 9; i++) soma += parseInt(cpf[i-1]) * (11 - i);
    resto = (soma * 10) % 11;
    if (resto === 10 || resto === 11) resto = 0;
    if (resto !== parseInt(cpf[9])) return false;
    soma = 0;
    for (let i = 1; i <= 10; i++) soma += parseInt(cpf[i-1]) * (12 - i);
    resto = (soma * 10) % 11;
    if (resto === 10 || resto === 11) resto = 0;
    return resto === parseInt(cpf[10]);
}

function renderHistorico() {
    const tbody = document.getElementById('tabela-historico');
    if (!tbody) return;
    if (historicoJogos.length === 0) {
        tbody.innerHTML = '<tr><td colspan="2" style="padding:10px;text-align:center;color:var(--text-muted);font-style:italic;">Nenhuma partida registrada.</td></tr>';
        return;
    }
    tbody.innerHTML = historicoJogos.map(j => `
        <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
            <td style="padding:10px;text-transform:capitalize;">${j.tipo}</td>
            <td style="padding:10px;color:${j.resultado === 'vitoria' ? 'var(--accent-green)' : j.resultado === 'empate' ? 'var(--text-secondary)' : 'var(--accent-red)'}">${j.resultado}</td>
        </tr>
    `).join('');
}

function verificarIdade(dataNasc) {
    if (!dataNasc) return false;
    const hoje = new Date(), nasc = new Date(dataNasc);
    let idade = hoje.getFullYear() - nasc.getFullYear();
    if (hoje.getMonth() - nasc.getMonth() < 0 ||
        (hoje.getMonth() === nasc.getMonth() && hoje.getDate() < nasc.getDate()))
        idade--;
    return idade >= 18;
}

function togglePrivacy(campo) {
    userProfile.privacidade[campo] = !userProfile.privacidade[campo];
    const estado = userProfile.privacidade[campo] ? 'Privado' : 'Público';
    showToast(`${campo} agora está ${estado}`, 'info');
}

function previewImagem(event) {
    const reader = new FileReader();
    reader.onload = () => { document.getElementById('previewFoto').src = reader.result; };
    if (event.target.files[0]) reader.readAsDataURL(event.target.files[0]);
}

async function salvarPerfil(event) {
    if (event) event.preventDefault();
    if (!userProfile.googleId) {
        showToast('Você precisa estar logado para salvar o perfil.', 'error');
        return;
    }
    const dataNasc = document.getElementById('dataNascimento').value;
    if (dataNasc && !verificarIdade(dataNasc)) {
        showToast('Você precisa ter 18 anos ou mais.', 'error');
        return;
    }
    const cpfValor = document.getElementById('cpf').value;
    if (cpfValor && !validarCPF(cpfValor)) {
        showToast('CPF inválido. Verifique os dígitos.', 'error');
        return;
    }
    userProfile.nick           = document.getElementById('nick').value;
    userProfile.bio            = document.getElementById('bio').value;
    userProfile.telefone       = document.getElementById('telefone').value;
    userProfile.cpf            = cpfValor;
    userProfile.dataNascimento = dataNasc;

    const btn = document.querySelector('#perfil-container .btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = 'Salvando...'; }

    const formData  = new FormData();
    const fileInput = document.getElementById('inputFoto');
    if (fileInput.files.length > 0) {
        const file = fileInput.files[0];
        if (file.size > 5 * 1024 * 1024) {
            showToast('A foto deve ter no máximo 5 MB.', 'error');
            if (btn) { btn.disabled = false; btn.textContent = 'Salvar Perfil'; }
            return;
        }
        formData.append('foto', file);
    }
    formData.append('googleId', userProfile.googleId);
    formData.append('dados', JSON.stringify(userProfile));

    try {
        const credential = localStorage.getItem('dr_credential');
        const headers    = credential ? { 'Authorization': `Bearer ${credential}` } : {};
        const res = await fetch(`${API}/update-profile`, { method: 'POST', headers, body: formData });
        if (res.ok) {
            const data = await res.json();
            showToast('Perfil atualizado com sucesso!', 'success');
            if (userProfile.nick) document.getElementById('userName').textContent = userProfile.nick;
            if (data.foto_url) {
                document.getElementById('userPicture').src = data.foto_url;
                document.getElementById('previewFoto').src = data.foto_url;
            }
        } else {
            const err = await res.json();
            showToast(`Erro: ${err.detail}`, 'error');
        }
    } catch (e) {
        console.error(e);
        showToast('Não foi possível salvar o perfil.', 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Salvar Perfil'; }
    }
}

// ============================================================
// INICIALIZAÇÃO
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    inicializarBotaoGoogle();
    verificarLoginPersistente();

    healthCheck();
    iniciarKeepalive();
    checkForInvite(); // Verifica se há convite na URL

    // Associa o botão de criar link privado
    const btnPrivate = document.getElementById('btnPrivateLink');
    if (btnPrivate) {
        btnPrivate.onclick = gerarLinkSalaPrivada;
    }

    // Inicializa o modal de compartilhamento
    inicializarModalCompartilhar();
});

async function verificarLoginPersistente() {
    const credential = localStorage.getItem('dr_credential');
    if (credential) {
        await processarLogin(credential);
    }
}

// ============================================================
// LÓGICA DE APOSTAS
// ============================================================
function showLobbyApostas() {
    mudarTela('screenLobbySalas');
    renderizarBotoesAposta();
    atualizarSaldoNaTelaAposta();
}

function renderizarBotoesAposta() {
    const grid = document.getElementById('apostaValoresGrid');
    if (!grid) return;
    grid.innerHTML = '';
    for (let valor of VALORES_APOSTA) {
        const premio = Math.floor(valor * 1.8);
        const disabled = (userMoedas < valor);
        const card = document.createElement('div');
        card.className = `aposta-card ${disabled ? 'disabled' : ''}`;
        card.innerHTML = `
            <div class="aposta-valor">🪙 ${valor} moedas</div>
            <div class="aposta-premio">prêmio: 🪙 ${premio} moedas</div>
        `;
        if (!disabled) {
            card.onclick = () => abrirModalConfirmarAposta(valor, premio);
        }
        grid.appendChild(card);
    }
}

function atualizarSaldoNaTelaAposta() {
    const spanReal = document.getElementById('apostaSaldoReal');
    const spanMoedas = document.getElementById('apostaMoedasDisplay');
    if (spanReal) spanReal.textContent = `R$ ${userSaldo.toFixed(2)}`;
    if (spanMoedas) spanMoedas.textContent = userMoedas;
}

function abrirModalConfirmarAposta(valor, premio) {
    apostaValorSelecionado = valor;
    const taxa = valor * 0.2;
    const saldoFinal = userSaldo - valor;
    document.getElementById('confirmaValor').textContent = `R$ ${valor.toFixed(2)}`;
    document.getElementById('confirmaPremio').textContent = `R$ ${premio.toFixed(2)}`;
    document.getElementById('confirmaTaxa').textContent = `R$ ${taxa.toFixed(2)}`;
    document.getElementById('confirmaSaldoFinal').textContent = `R$ ${saldoFinal.toFixed(2)}`;
    document.getElementById('modalConfirmarAposta').style.display = 'flex';
}

function fecharModalConfirmarAposta() {
    document.getElementById('modalConfirmarAposta').style.display = 'none';
}

document.getElementById('btnConfirmarAposta').onclick = async () => {
    fecharModalConfirmarAposta();
    await entrarFilaAposta(apostaValorSelecionado);
};

async function entrarFilaAposta(valor) {
    const credential = localStorage.getItem('dr_credential');
    if (!credential || !userProfile.googleId) {
        showToast("Você precisa estar logado.", "error");
        return;
    }
    try {
        const res = await fetch(`${API}/api/aposta/entrar-fila`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${credential}`
            },
            body: JSON.stringify({ googleId: userProfile.googleId, valor })
        });
        const data = await res.json();
        if (res.ok) {
            if (data.status === 'pareado') {
                conectarPartidaAposta(data.game_id, data.sala_id, valor, data.premio, data.color);
            } else {
                apostaSalaId = data.sala_id;
                document.getElementById('aguardandoValor').textContent = `🪙 ${valor} moedas`;
                document.getElementById('modalAguardandoOponente').style.display = 'flex';
                iniciarPollingAposta(data.sala_id, valor);
            }
        } else {
            showToast(data.detail || "Erro ao entrar na fila.", "error");
        }
    } catch (e) {
        showToast("Erro de conexão.", "error");
    }
}

function iniciarPollingAposta(salaId, valor) {
    if (apostaPollingInterval) clearInterval(apostaPollingInterval);
    const TIMEOUT_MS = 5 * 60 * 1000;
    const inicioPolling = Date.now();

    apostaPollingInterval = setInterval(async () => {
        try {
            if (Date.now() - inicioPolling > TIMEOUT_MS) {
                clearInterval(apostaPollingInterval);
                showToast("Tempo esgotado. Nenhum adversário encontrado.", "error");
                cancelarBuscaAposta();
                return;
            }
            const res = await fetch(`${API}/api/aposta/status/${salaId}`);
            const data = await res.json();
            if (data.status === 'em_jogo') {
                clearInterval(apostaPollingInterval);
                const minhaCor = data.jogador1_id === userProfile.googleId
                    ? data.cor_jogador1
                    : data.cor_jogador2;
                document.getElementById('modalAguardandoOponente').style.display = 'none';
                conectarPartidaAposta(data.game_id, salaId, valor, data.premio, minhaCor);
            } else if (data.status === 'cancelada') {
                clearInterval(apostaPollingInterval);
                document.getElementById('modalAguardandoOponente').style.display = 'none';
                showToast("Sala cancelada.", "error");
            }
        } catch (e) {}
    }, 2000);
}

async function cancelarBuscaAposta() {
    if (apostaPollingInterval) clearInterval(apostaPollingInterval);
    const credential = localStorage.getItem('dr_credential');
    try {
        await fetch(`${API}/api/aposta/cancelar-fila`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(credential ? { 'Authorization': `Bearer ${credential}` } : {})
            },
            body: JSON.stringify({ googleId: userProfile.googleId })
        });
        showToast("Busca cancelada.", "info");
    } catch (e) {
        console.error("Erro ao cancelar busca:", e);
    } finally {
        document.getElementById('modalAguardandoOponente').style.display = 'none';
        mudarTela('screenLobbySalas');
    }
}

function conectarPartidaAposta(gameId, salaId, valorEntrada, premio, minhaCor) {
    if (ws) ws.close();
    modoAtual = 'aposta';
    window.apostaInfo = { valor: valorEntrada, premio: premio };
    capturedByWhite = 0;
    capturedByBlack = 0;
    currentBoard = [];
    partidaIdAtual = null;
    partidaRegistrada = false;
    minhaCorAtual = minhaCor;

    limparChat();
    const wsProtocol = API.startsWith('https') ? 'wss' : 'ws';
    ws = new WebSocket(`${wsProtocol}://${API.split('://')[1]}/ws/aposta/${salaId}/${minhaCor}`);

    ws.onopen = () => {
        mudarTela('screenGame');
        renderizarCoordenadas();
        renderCaptureDots();
        const btnAbandono = document.getElementById('btn-surrender');
        if (btnAbandono) {
            btnAbandono.textContent = '🏳 Abandonar';
            btnAbandono.onclick = confirmarAbandono;
        }
        showToast(`Aposta de ${valorEntrada} moedas — prêmio ${premio} moedas`, "success", 5000);
        let banner = document.getElementById('apostaBanner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'apostaBanner';
            banner.className = 'aposta-banner';
            const topbar = document.querySelector('.game-topbar');
            topbar.parentNode.insertBefore(banner, topbar);
        }
        banner.innerHTML = `🪙 APOSTA: ${valorEntrada} moedas → Prêmio: ${premio} moedas`;
        banner.style.display = 'block';
    };
    ws.onmessage = (e) => onMensagemServidor(JSON.parse(e.data), minhaCorAtual);
    ws.onclose = () => {
        if (modoAtual === 'aposta') {
            const modalAberto = document.getElementById('modalFimJogo')?.classList.contains('open');
            if (!modalAberto) {
                showToast("Partida encerrada.", "info");
                mudarTela('screenMenu');
            }
        }
    };
}

function oferecerRevancheAposta() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'rematch_offer' }));
        showToast("Proposta de revanche enviada.", "info");
    }
}

function aceitarRevancheAposta() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'rematch_accept' }));
    }
}