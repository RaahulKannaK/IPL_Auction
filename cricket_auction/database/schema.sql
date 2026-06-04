CREATE DATABASE IF NOT EXISTS cricket_auction;
USE cricket_auction;

-- ==========================================
-- USERS
-- ==========================================

CREATE TABLE users (
id INT AUTO_INCREMENT PRIMARY KEY,
username VARCHAR(50) UNIQUE NOT NULL,
password_hash VARCHAR(255) NOT NULL,


role ENUM(
    'admin',
    'auctioneer',
    'team_owner',
    'viewer'
) DEFAULT 'viewer',

created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP


);

-- ==========================================
-- AUCTIONS / LEAGUES
-- ==========================================

CREATE TABLE auctions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    
    league_name VARCHAR(100) NOT NULL,
    
    status ENUM(
        'pending',
        'live',
        'paused',
        'completed'
    ) DEFAULT 'pending',
    
    squad_size INT DEFAULT 18,
    purse_limit DECIMAL(10,2) DEFAULT 100.00,
    
    overseas_limit INT DEFAULT 8,
    
    created_by INT NOT NULL,
    
    current_player_id INT NULL,
    current_bidder_id INT NULL,
    current_bid DECIMAL(10,2) DEFAULT 0.00,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (current_bidder_id) REFERENCES teams(id)
    
);
-- ==========================================
-- TEAMS
-- Every team belongs to an auction
-- ==========================================

CREATE TABLE teams (
id INT AUTO_INCREMENT PRIMARY KEY,


auction_id INT NOT NULL,

team_name VARCHAR(100) NOT NULL,

owner_id INT NULL,

purse_limit DECIMAL(10,2) DEFAULT 100.00,

spent DECIMAL(10,2) DEFAULT 0.00,

reserved DECIMAL(10,2) DEFAULT 0.00,

created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

FOREIGN KEY (auction_id)
REFERENCES auctions(id),

FOREIGN KEY (owner_id)
REFERENCES users(id)
 

);

-- ==========================================
-- MASTER PLAYER DATABASE
-- Stored only once globally
-- ==========================================

CREATE TABLE players (
id INT AUTO_INCREMENT PRIMARY KEY,

 
player_name VARCHAR(100) NOT NULL,

category ENUM(
    'batsman',
    'bowler',
    'all_rounder',
    'wicket_keeper'
) NOT NULL,

overseas BOOLEAN DEFAULT FALSE,

image_url VARCHAR(255),

created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
 

);

-- ==========================================
-- PLAYERS INCLUDED IN A PARTICULAR AUCTION
-- ==========================================

CREATE TABLE auction_players (
id INT AUTO_INCREMENT PRIMARY KEY,

 
auction_id INT NOT NULL,

player_id INT NOT NULL,

base_price DECIMAL(10,2) DEFAULT 2.00,

status ENUM(
    'available',
    'in_auction',
    'sold',
    'unsold'
) DEFAULT 'available',

sold_team_id INT NULL,

sold_price DECIMAL(10,2) NULL,

FOREIGN KEY (auction_id)
REFERENCES auctions(id),

FOREIGN KEY (player_id)
REFERENCES players(id),

FOREIGN KEY (sold_team_id)
REFERENCES teams(id)
 

);

-- ==========================================
-- AUCTION SESSIONS
-- For Pause / Resume
-- Parallel Auction Concept
-- ==========================================

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
-- ==========================================
-- BIDS
-- Visible bid history
-- ==========================================

CREATE TABLE bids (
id INT AUTO_INCREMENT PRIMARY KEY,

 
auction_id INT NOT NULL,

auction_player_id INT NOT NULL,

team_id INT NOT NULL,

bid_amount DECIMAL(10,2) NOT NULL,

session_id INT NULL,

created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

FOREIGN KEY (auction_id)
REFERENCES auctions(id),

FOREIGN KEY (auction_player_id)
REFERENCES auction_players(id),

FOREIGN KEY (team_id)
REFERENCES teams(id),

FOREIGN KEY (session_id)
REFERENCES auction_sessions(id)
 

);

-- ==========================================
-- HIDDEN MAXIMUM BIDS
-- Core Feature
-- Only visible to that team
-- ==========================================

CREATE TABLE hidden_max_bids (
id INT AUTO_INCREMENT PRIMARY KEY,

 
auction_id INT NOT NULL,

auction_player_id INT NOT NULL,

team_id INT NOT NULL,

max_bid DECIMAL(10,2) NOT NULL,

is_active BOOLEAN DEFAULT TRUE,

created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

FOREIGN KEY (auction_id)
REFERENCES auctions(id),

FOREIGN KEY (auction_player_id)
REFERENCES auction_players(id),

FOREIGN KEY (team_id)
REFERENCES teams(id)
 

);

-- ==========================================
-- RESERVED PURSE TRACKING
-- Core Feature
-- ==========================================

CREATE TABLE purse_reservations (
id INT AUTO_INCREMENT PRIMARY KEY,

 
team_id INT NOT NULL,

auction_player_id INT NOT NULL,

reserved_amount DECIMAL(10,2) NOT NULL,

status ENUM(
    'active',
    'released',
    'converted'
) DEFAULT 'active',

created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

FOREIGN KEY (team_id)
REFERENCES teams(id),

FOREIGN KEY (auction_player_id)
REFERENCES auction_players(id)
 

);

-- ==========================================
-- SOLD PLAYERS
-- Final ownership record
-- ==========================================

CREATE TABLE team_players (
id INT AUTO_INCREMENT PRIMARY KEY,

 
team_id INT NOT NULL,

auction_player_id INT NOT NULL,

purchase_price DECIMAL(10,2) NOT NULL,

purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

FOREIGN KEY (team_id)
REFERENCES teams(id),

FOREIGN KEY (auction_player_id)
REFERENCES auction_players(id)
 

);

-- ==========================================
-- AUCTION ACTION HISTORY
-- Undo Sale / Audit Log
-- ==========================================

CREATE TABLE auction_history (
id INT AUTO_INCREMENT PRIMARY KEY,

 
auction_id INT NOT NULL,

action_type VARCHAR(50),

action_data JSON,

performed_by INT,

created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

FOREIGN KEY (auction_id)
REFERENCES auctions(id),

FOREIGN KEY (performed_by)
REFERENCES users(id)
 

);

-- ==========================================
-- PLAYING XI
-- ==========================================

CREATE TABLE playing11 (
id INT AUTO_INCREMENT PRIMARY KEY,

 
team_id INT NOT NULL,

player_id INT NOT NULL,

position INT NOT NULL,

is_captain BOOLEAN DEFAULT FALSE,

is_vice_captain BOOLEAN DEFAULT FALSE,

FOREIGN KEY (team_id)
REFERENCES teams(id),

FOREIGN KEY (player_id)
REFERENCES players(id)
 

);


cursor.execute("""
    SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status, ap.sold_price
    FROM players p
    JOIN auction_players ap ON p.id = ap.player_id
    WHERE ap.auction_id = %s AND ap.status IN ('available', 'unsold')
    ORDER BY RAND()
""", (auction['id'],))