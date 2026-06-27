-- 1. USERS (no dependencies)
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('admin', 'auctioneer', 'team_owner', 'viewer') DEFAULT 'viewer',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. PLAYERS (no dependencies)
CREATE TABLE players (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_name VARCHAR(100) NOT NULL,
    category ENUM('batsman', 'bowler', 'all_rounder', 'wicket_keeper') NOT NULL,
    overseas BOOLEAN DEFAULT FALSE,
    image_url VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. AUCTIONS (NO FK to teams yet - remove current_bidder_id FK temporarily)
CREATE TABLE auctions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    league_name VARCHAR(100) NOT NULL,
    status ENUM('pending', 'live', 'paused', 'completed') DEFAULT 'pending',
    squad_size INT DEFAULT 18,
    purse_limit DECIMAL(10,2) DEFAULT 100.00,
    overseas_limit INT DEFAULT 8,
    created_by INT NOT NULL,
    current_player_id INT NULL,
    current_bidder_id INT NULL,
    current_bid DECIMAL(10,2) DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

-- 4. TEAMS (now auctions exists)
CREATE TABLE teams (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auction_id INT NOT NULL,
    team_name VARCHAR(100) NOT NULL,
    owner_id INT NULL,
    purse_limit DECIMAL(10,2) DEFAULT 100.00,
    spent DECIMAL(10,2) DEFAULT 0.00,
    reserved DECIMAL(10,2) DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (auction_id) REFERENCES auctions(id),
    FOREIGN KEY (owner_id) REFERENCES users(id)
);

-- 5. Add missing FK to auctions now that teams exists
ALTER TABLE auctions ADD FOREIGN KEY (current_bidder_id) REFERENCES teams(id);

-- 6. AUCTION_PLAYERS
CREATE TABLE auction_players (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auction_id INT NOT NULL,
    player_id INT NOT NULL,
    base_price DECIMAL(10,2) DEFAULT 2.00,
    status ENUM('available', 'in_auction', 'sold', 'unsold') DEFAULT 'available',
    sold_team_id INT NULL,
    sold_price DECIMAL(10,2) NULL,
    skip_reason VARCHAR(50) NULL,
    skip_notes TEXT NULL,
    FOREIGN KEY (auction_id) REFERENCES auctions(id),
    FOREIGN KEY (player_id) REFERENCES players(id),
    FOREIGN KEY (sold_team_id) REFERENCES teams(id)
);

-- 7. AUCTION_SESSIONS
CREATE TABLE auction_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auction_id INT NOT NULL,
    session_name VARCHAR(100),
    status ENUM('active', 'paused', 'completed') DEFAULT 'active',
    start_time DATETIME NULL,
    end_time DATETIME NULL,
    team_ids JSON NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (auction_id) REFERENCES auctions(id)
);

-- 8. BIDS
CREATE TABLE bids (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auction_id INT NOT NULL,
    auction_player_id INT NOT NULL,
    team_id INT NOT NULL,
    bid_amount DECIMAL(10,2) NOT NULL,
    session_id INT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (auction_id) REFERENCES auctions(id),
    FOREIGN KEY (auction_player_id) REFERENCES auction_players(id),
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (session_id) REFERENCES auction_sessions(id)
);

-- 9. HIDDEN_MAX_BIDS
CREATE TABLE hidden_max_bids (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auction_id INT NOT NULL,
    auction_player_id INT NOT NULL,
    team_id INT NOT NULL,
    max_bid DECIMAL(10,2) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (auction_id) REFERENCES auctions(id),
    FOREIGN KEY (auction_player_id) REFERENCES auction_players(id),
    FOREIGN KEY (team_id) REFERENCES teams(id)
);

-- 10. PURSE_RESERVATIONS
CREATE TABLE purse_reservations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NOT NULL,
    auction_player_id INT NOT NULL,
    reserved_amount DECIMAL(10,2) NOT NULL,
    status ENUM('active', 'released', 'converted') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (auction_player_id) REFERENCES auction_players(id)
);

-- 11. TEAM_PLAYERS
CREATE TABLE team_players (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NOT NULL,
    auction_player_id INT NOT NULL,
    purchase_price DECIMAL(10,2) NOT NULL,
    purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (auction_player_id) REFERENCES auction_players(id)
);

-- 12. AUCTION_HISTORY
CREATE TABLE auction_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auction_id INT NOT NULL,
    action_type VARCHAR(50),
    action_data JSON,
    performed_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (auction_id) REFERENCES auctions(id),
    FOREIGN KEY (performed_by) REFERENCES users(id)
);

-- 13. PLAYING11
CREATE TABLE playing11 (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NOT NULL,
    player_id INT NOT NULL,
    position INT NOT NULL,
    is_captain BOOLEAN DEFAULT FALSE,
    is_vice_captain BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (player_id) REFERENCES players(id)
);

-- 14. PLAYER_SKIPS
CREATE TABLE player_skips (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auction_id INT NOT NULL,
    auction_player_id INT NOT NULL,
    player_id INT NOT NULL,
    team_id INT NULL,
    reason VARCHAR(50) NOT NULL,
    notes TEXT,
    skipped_by INT NOT NULL,
    skipped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (auction_id) REFERENCES auctions(id),
    FOREIGN KEY (auction_player_id) REFERENCES auction_players(id),
    FOREIGN KEY (player_id) REFERENCES players(id),
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (skipped_by) REFERENCES users(id)
);

-- 15. Extra columns for auctions
ALTER TABLE auctions 
ADD COLUMN last_sold_team_id INT NULL,
ADD COLUMN last_sold_player_name VARCHAR(255) NULL,
ADD COLUMN last_sold_price DECIMAL(10,2) NULL,
ADD COLUMN last_sold_auction_player_id INT NULL,
ADD COLUMN last_sold_at TIMESTAMP NULL;


CREATE TABLE team_owners (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NOT NULL,
    user_id INT NOT NULL,
    is_primary BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE KEY unique_team_user (team_id, user_id)
);

ALTER TABLE teams ADD COLUMN owner_ids JSON NULL AFTER owner_id;

-- SESSION_PLAYERS: Links players to specific auction sessions with session-local status
CREATE TABLE session_players (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id INT NOT NULL,
    player_id INT NOT NULL,
    base_price DECIMAL(10,2) DEFAULT 2.00,
    status ENUM('available', 'in_auction', 'sold', 'unsold') DEFAULT 'available',
    sold_team_id INT NULL,
    sold_price DECIMAL(10,2) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES auction_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE,
    FOREIGN KEY (sold_team_id) REFERENCES teams(id),
    UNIQUE KEY unique_session_player (session_id, player_id)
);

-- Add session_id to auction_players to track which session a player was originally assigned to
ALTER TABLE auction_players ADD COLUMN session_id INT NULL AFTER auction_id;
ALTER TABLE auction_players ADD FOREIGN KEY (session_id) REFERENCES auction_sessions(id);

-- ============================================================
-- 1. BIDS TABLE: Add session_player_id for session-scoped bidding
-- ============================================================
ALTER TABLE bids 
ADD COLUMN session_player_id INT NULL AFTER auction_player_id;

ALTER TABLE bids 
ADD FOREIGN KEY (session_player_id) REFERENCES session_players(id);

-- Make auction_player_id nullable since we'll use session_player_id instead
ALTER TABLE bids 
MODIFY auction_player_id INT NULL;

-- Add index for performance
CREATE INDEX idx_bids_session ON bids(session_id, session_player_id);


-- ============================================================
-- 2. PLAYER_SKIPS TABLE: Add session_id and session_player_id
-- ============================================================
ALTER TABLE player_skips 
ADD COLUMN session_id INT NULL AFTER auction_id;

ALTER TABLE player_skips 
ADD COLUMN session_player_id INT NULL AFTER auction_player_id;

ALTER TABLE player_skips 
ADD FOREIGN KEY (session_id) REFERENCES auction_sessions(id);

ALTER TABLE player_skips 
ADD FOREIGN KEY (session_player_id) REFERENCES session_players(id);

-- Make old columns nullable
ALTER TABLE player_skips 
MODIFY auction_player_id INT NULL;

ALTER TABLE player_skips 
MODIFY player_id INT NULL;

-- Add index
CREATE INDEX idx_skips_session ON player_skips(session_id, session_player_id);


-- ============================================================
-- 3. HIDDEN_MAX_BIDS TABLE: Add session_player_id
-- ============================================================
ALTER TABLE hidden_max_bids 
ADD COLUMN session_player_id INT NULL AFTER auction_player_id;

ALTER TABLE hidden_max_bids 
ADD FOREIGN KEY (session_player_id) REFERENCES session_players(id);

ALTER TABLE hidden_max_bids 
MODIFY auction_player_id INT NULL;

CREATE INDEX idx_hidden_session ON hidden_max_bids(session_player_id, team_id);


-- ============================================================
-- 4. PURSE_RESERVATIONS TABLE: Add session_player_id
-- ============================================================
ALTER TABLE purse_reservations 
ADD COLUMN session_player_id INT NULL AFTER auction_player_id;

ALTER TABLE purse_reservations 
ADD FOREIGN KEY (session_player_id) REFERENCES session_players(id);

ALTER TABLE purse_reservations 
MODIFY auction_player_id INT NULL;

CREATE INDEX idx_purse_session ON purse_reservations(session_player_id, team_id);


-- ============================================================
-- 5. TEAM_PLAYERS TABLE: Add session_player_id
-- ============================================================
ALTER TABLE team_players 
ADD COLUMN session_player_id INT NULL AFTER auction_player_id;

ALTER TABLE team_players 
ADD FOREIGN KEY (session_player_id) REFERENCES session_players(id);

ALTER TABLE team_players 
MODIFY auction_player_id INT NULL;

CREATE INDEX idx_team_players_session ON team_players(session_player_id);


-- ============================================================
-- 6. AUCTIONS TABLE: Add last_sold_session_player_id
-- ============================================================
ALTER TABLE auctions 
ADD COLUMN last_sold_session_player_id INT NULL AFTER last_sold_auction_player_id;

-- Note: last_sold_auction_player_id stays for backward compat
-- last_sold_session_player_id is the new primary field for session-scoped tracking


-- ============================================================
-- 7. AUCTION_SESSIONS TABLE: Add current tracking columns
-- ============================================================
-- These let each session track its own current player/bid independently
ALTER TABLE auction_sessions 
ADD COLUMN current_player_id INT NULL AFTER team_ids;

ALTER TABLE auction_sessions 
ADD COLUMN current_bid DECIMAL(10,2) DEFAULT 0.00 AFTER current_player_id;

ALTER TABLE auction_sessions 
ADD COLUMN current_bidder_id INT NULL AFTER current_bid;

ALTER TABLE auction_sessions 
ADD FOREIGN KEY (current_bidder_id) REFERENCES teams(id);

-- This is CRITICAL: allows each session to have independent bidding state
-- Instead of using auctions.current_player_id for everything


-- ============================================================
-- 8. SESSION_PLAYERS TABLE: Add skip_reason for session-local skips
-- ============================================================
ALTER TABLE session_players 
ADD COLUMN skip_reason VARCHAR(50) NULL AFTER sold_price;

ALTER TABLE session_players 
ADD COLUMN skip_notes TEXT NULL AFTER skip_reason;


-- ============================================================
-- 9. BIDS TABLE: Add session_id foreign key (already exists but verify)
-- ============================================================
-- session_id already exists in your schema, just ensure FK is there
ALTER TABLE bids 
ADD FOREIGN KEY (session_id) REFERENCES auction_sessions(id);

-- Add composite index for common lookups
CREATE INDEX idx_bids_auction_session ON bids(auction_id, session_id);






-- Run this FIRST (keep your existing tables, add these new ones)

-- ============================================================
-- NEW: Session-specific bids (clean, no nullable FKs)
-- ============================================================
CREATE TABLE session_bids (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id INT NOT NULL,
    session_player_id INT NOT NULL,
    team_id INT NOT NULL,
    bid_amount DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES auction_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (session_player_id) REFERENCES session_players(id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES teams(id),
    INDEX idx_session_bids_lookup (session_id, session_player_id),
    INDEX idx_session_bids_team (session_id, team_id)
);

-- ============================================================
-- NEW: Session-specific skips
-- ============================================================
CREATE TABLE session_skips (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id INT NOT NULL,
    session_player_id INT NOT NULL,
    team_id INT NOT NULL,
    reason VARCHAR(50) DEFAULT 'manual',
    notes TEXT,
    skipped_by INT NOT NULL,
    skipped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES auction_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (session_player_id) REFERENCES session_players(id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (skipped_by) REFERENCES users(id),
    UNIQUE KEY unique_session_skip (session_id, session_player_id, team_id)
);

-- ============================================================
-- NEW: Session-specific team assignments (sold players)
-- ============================================================
CREATE TABLE session_team_players (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NOT NULL,
    session_player_id INT NOT NULL,
    purchase_price DECIMAL(10,2) NOT NULL,
    purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (session_player_id) REFERENCES session_players(id) ON DELETE CASCADE,
    UNIQUE KEY unique_session_team_player (team_id, session_player_id)
);

-- ============================================================
-- NEW: Session-specific hidden max bids
-- ============================================================
CREATE TABLE session_hidden_max_bids (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id INT NOT NULL,
    session_player_id INT NOT NULL,
    team_id INT NOT NULL,
    max_bid DECIMAL(10,2) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES auction_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (session_player_id) REFERENCES session_players(id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES teams(id),
    INDEX idx_hidden_lookup (session_player_id, team_id)
);

-- ============================================================
-- NEW: Session-specific purse reservations
-- ============================================================
CREATE TABLE session_purse_reservations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NOT NULL,
    session_player_id INT NOT NULL,
    reserved_amount DECIMAL(10,2) NOT NULL,
    status ENUM('active', 'released', 'converted') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (session_player_id) REFERENCES session_players(id) ON DELETE CASCADE,
    INDEX idx_purse_team (team_id, session_player_id)
);

-- Add willing_price column to session_team_players
ALTER TABLE session_team_players 
ADD COLUMN willing_price DECIMAL(10,2) NULL AFTER purchase_price;

-- Add these indexes to prevent table scans
CREATE INDEX idx_auction_sessions_lookup ON auction_sessions(id, auction_id, current_player_id);
CREATE INDEX idx_session_bids_lookup ON session_bids(session_id, session_player_id, created_at);
CREATE INDEX idx_session_skips_lookup ON session_skips(session_id, session_player_id, team_id);
CREATE INDEX idx_session_players_lookup ON session_players(id, session_id, player_id);


CREATE TABLE IF NOT EXISTS pending_willing_price (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    team_id INT NOT NULL,
    player_id INT NOT NULL,
    session_player_id INT NOT NULL,
    player_name VARCHAR(100) NOT NULL,
    purchase_price DECIMAL(10,2) NOT NULL,
    popup_shown TINYINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (player_id) REFERENCES players(id),
    FOREIGN KEY (session_player_id) REFERENCES session_players(id)
);