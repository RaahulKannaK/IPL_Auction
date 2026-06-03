CREATE DATABASE IF NOT EXISTS cricket_auction;
USE cricket_auction;

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    role ENUM('owner', 'admin', 'auctioneer', 'team_owner', 'viewer') DEFAULT 'viewer',
    team_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE teams (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    purse DECIMAL(10,2) DEFAULT 100.00,
    spent DECIMAL(10,2) DEFAULT 0.00,
    reserved DECIMAL(10,2) DEFAULT 0.00,
    owner_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE players (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    category ENUM('batsman', 'bowler', 'all_rounder', 'wicket_keeper'),
    base_price DECIMAL(10,2) DEFAULT 2.00,
    overseas BOOLEAN DEFAULT FALSE,
    image_url VARCHAR(255),
    status ENUM('unsold', 'sold', 'in_auction') DEFAULT 'unsold',
    team_id INT,
    sold_price DECIMAL(10,2)
);

CREATE TABLE auctions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    league_name VARCHAR(100) NOT NULL,
    status ENUM('pending', 'live', 'paused', 'completed') DEFAULT 'pending',
    current_player_id INT,
    current_bid DECIMAL(10,2) DEFAULT 0,
    current_bidder_id INT,
    created_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bids (
    id INT AUTO_INCREMENT PRIMARY KEY,
    auction_id INT,
    player_id INT,
    team_id INT,
    amount DECIMAL(10,2),
    is_hidden_max BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);