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