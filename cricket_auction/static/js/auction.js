// static/js/auction.js — Fixed polling

const CONFIG = {
    auctionId: window.AUCTION_ID || null,
    sessionId: window.SESSION_ID || null,
    teamId: window.TEAM_ID || null,
    pollInterval: 1500,
    isPolling: false,
    lastHash: null,
    abortCtrl: null
};

async function pollStatus() {
    if (!CONFIG.auctionId || !CONFIG.sessionId) {
        console.error('Missing auction_id or session_id — check window.AUCTION_ID and window.SESSION_ID');
        return;
    }
    if (CONFIG.isPolling) return;
    CONFIG.isPolling = true;

    if (CONFIG.abortCtrl) CONFIG.abortCtrl.abort();
    CONFIG.abortCtrl = new AbortController();

    try {
        const url = `/admin/auction/status?auction_id=${CONFIG.auctionId}&session_id=${CONFIG.sessionId}&team_id=${CONFIG.teamId || ''}`;
        
        const res = await fetch(url, {
            signal: CONFIG.abortCtrl.signal,
            headers: { 'Accept': 'application/json' }
        });
        
        if (!res.ok) {
            if (res.status === 404) {
                console.error(`404: auction_id=${CONFIG.auctionId} not found`);
            }
            throw new Error(`HTTP ${res.status}`);
        }
        
        const data = await res.json();
        
        const hash = JSON.stringify(data);
        if (hash !== CONFIG.lastHash) {
            CONFIG.lastHash = hash;
            renderUI(data);
        }
    } catch (e) {
        if (e.name !== 'AbortError') console.error('Poll error:', e);
    } finally {
        CONFIG.isPolling = false;
    }
}

function renderUI(data) {
    // Status badge
    const statusBadge = document.getElementById('status-badge');
    if (statusBadge) {
        const isLive = data.status === 'live' && data.session_status === 'active';
        statusBadge.className = `live-status ${isLive ? 'status-live' : 'status-paused'}`;
        statusBadge.innerHTML = isLive 
            ? '<span class="live-dot"></span> LIVE'
            : data.status.toUpperCase();
    }
    
    // Current player card
    const playerCard = document.getElementById('current-player');
    if (playerCard && data.current_player) {
        const currentId = playerCard.dataset.playerId;
        if (currentId !== String(data.current_player.id)) {
            playerCard.style.opacity = '0';
            setTimeout(() => {
                playerCard.innerHTML = `
                    <div class="player-avatar">${data.current_player.name[0]}</div>
                    <div class="player-name">${data.current_player.name}</div>
                    <div class="player-meta">
                        <span class="cat-badge badge-${data.current_player.category}">${data.current_player.category}</span>
                        ${data.current_player.overseas ? '<span class="cat-badge badge-overseas">OVERSEAS</span>' : ''}
                    </div>
                    <div class="bid-value-gold">₹${data.current_player.current_bid.toFixed(2)}Cr</div>
                    <div class="current-bidder">${data.current_player.current_bidder || 'No bids yet'}</div>
                `;
                playerCard.dataset.playerId = data.current_player.id;
                playerCard.style.opacity = '1';
            }, 150);
        } else {
            const bidEl = document.querySelector('.bid-value-gold');
            const bidderEl = document.querySelector('.current-bidder');
            if (bidEl) bidEl.textContent = `₹${data.current_player.current_bid.toFixed(2)}Cr`;
            if (bidderEl) bidderEl.textContent = data.current_player.current_bidder || 'No bids yet';
        }
    } else if (playerCard && !data.current_player) {
        playerCard.innerHTML = '<div class="no-player">No player selected</div>';
        playerCard.dataset.playerId = '';
    }
    
    // Bid history
    if (data.bid_history && data.bid_history.length > 0) {
        const latest = data.bid_history[0];
        const historyList = document.getElementById('bid-history');
        if (historyList && !historyList.querySelector(`[data-time="${latest.time}"]`)) {
            const item = document.createElement('div');
            item.className = 'history-item';
            item.dataset.time = latest.time;
            item.innerHTML = `
                <span class="history-bidder">${latest.bidder}</span>
                <span class="history-amount">₹${latest.amount.toFixed(2)}Cr</span>
            `;
            historyList.insertBefore(item, historyList.firstChild);
            if (historyList.children.length > 10) historyList.lastChild.remove();
        }
    }
    
    // Teams list
    if (data.teams && typeof updateTeamsList === 'function') {
        updateTeamsList(data.teams, data.my_team);
    }
    
    // My team info (for team owner)
    if (data.my_team) {
        const purseEl = document.getElementById('my-purse');
        if (purseEl) purseEl.textContent = `₹${data.my_team.remaining_purse.toFixed(2)}Cr`;
        
        const bidderBadge = document.getElementById('my-bidder-badge');
        if (bidderBadge) {
            bidderBadge.style.display = data.my_team.is_current_bidder ? 'inline' : 'none';
        }
    }
}

// Start polling
if (CONFIG.auctionId && CONFIG.sessionId) {
    setInterval(pollStatus, CONFIG.pollInterval);
    pollStatus();
} else {
    console.error('Cannot start polling: missing auction_id or session_id');
}