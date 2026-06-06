-- ==========================================
-- USERS
-- ==========================================

INSERT INTO users (username, password_hash, role)
VALUES
('admin', '12345678', 'admin'),
('auctioneer1', '12345678', 'auctioneer'),

('karthik', '12345678', 'team_owner'),
('rahul', '12345678', 'team_owner'),
('vikram', '12345678', 'team_owner'),
('arjun', '12345678', 'team_owner'),

('viewer1', '12345678', 'viewer');

-- ==========================================
-- AUCTION
-- ==========================================

INSERT INTO auctions (
    league_name,
    status,
    squad_size,
    purse_limit,
    overseas_limit,
    created_by
)
VALUES (
    'IPL Auction 2024',
    'pending',
    25,
    100.00,
    8,
    1
);

-- ==========================================
-- TEAMS
-- ==========================================

INSERT INTO teams (
    team_name,
    purse_limit,
    spent,
    reserved
)
VALUES
('Mumbai Indians', 100.00, 0.00, 0.00),
('Chennai Super Kings', 100.00, 0.00, 0.00),
('Royal Challengers Bangalore', 100.00, 0.00, 0.00),
('Delhi Capitals', 100.00, 0.00, 0.00);

-- ==========================================
-- TEAM OWNERS
-- ==========================================

INSERT INTO team_owners (
    auction_id,
    team_id,
    user_id,
    assigned_by
)
VALUES
(1, 1, 3, 1),
(1, 2, 4, 1),
(1, 3, 5, 1),
(1, 4, 6, 1);

-- ==========================================
-- MASTER PLAYERS
-- ==========================================

INSERT INTO players (
    player_name,
    category,
    overseas
)
VALUES
('Virat Kohli', 'batsman', FALSE),
('Jasprit Bumrah', 'bowler', FALSE),
('MS Dhoni', 'wicket_keeper', FALSE),
('Rohit Sharma', 'batsman', FALSE),

('Pat Cummins', 'bowler', TRUE),
('Ben Stokes', 'all_rounder', TRUE),
('Kane Williamson', 'batsman', TRUE),
('Rashid Khan', 'bowler', TRUE),

('Hardik Pandya', 'all_rounder', FALSE),
('Rishabh Pant', 'wicket_keeper', FALSE),
('Shubman Gill', 'batsman', FALSE),
('Mohammed Shami', 'bowler', FALSE),

('Andre Russell', 'all_rounder', TRUE),
('Quinton de Kock', 'wicket_keeper', TRUE),
('KL Rahul', 'batsman', FALSE);

-- ==========================================
-- AUCTION PLAYERS
-- ==========================================

INSERT INTO auction_players (
    auction_id,
    player_id,
    base_price,
    status
)
SELECT
    1,
    id,
    2.00,
    'available'
FROM players;

-- ==========================================
-- AUCTION SESSION
-- ==========================================

INSERT INTO auction_sessions (
    auction_id,
    session_name,
    status,
    start_time
)
VALUES (
    1,
    'Weekend Session 1',
    'active',
    NOW()
);

-- ==========================================
-- SAMPLE HIDDEN MAX BID
-- ==========================================

INSERT INTO hidden_max_bids (
    auction_id,
    auction_player_id,
    team_id,
    max_bid
)
VALUES (
    1,
    2,
    1,
    12.00
);

-- ==========================================
-- SAMPLE RESERVED PURSE
-- ==========================================

INSERT INTO purse_reservations (
    team_id,
    auction_player_id,
    reserved_amount,
    status
)
VALUES (
    1,
    2,
    12.00,
    'active'
);

-- ==========================================
-- SAMPLE BID
-- ==========================================

INSERT INTO bids (
    auction_id,
    auction_player_id,
    team_id,
    bid_amount,
    session_id
)
VALUES (
    1,
    2,
    1,
    8.00,
    1
);

-- ==========================================
-- SAMPLE TEAM PLAYER
-- ==========================================

INSERT INTO team_players (
    team_id,
    auction_player_id,
    purchase_price
)
VALUES (
    1,
    2,
    8.00
);

-- ==========================================
-- SAMPLE PLAYER SKIP
-- ==========================================

INSERT INTO player_skips (
    auction_id,
    auction_player_id,
    player_id,
    team_id,
    reason,
    notes,
    skipped_by
)
VALUES (
    1,
    3,
    3,
    NULL,
    'no_bids',
    'No team showed interest',
    2
);

-- ==========================================
-- SAMPLE AUDIT ENTRY
-- ==========================================

INSERT INTO auction_history (
    auction_id,
    action_type,
    action_data,
    performed_by
)
VALUES (
    1,
    'CREATE_AUCTION',
    JSON_OBJECT(
        'auction_name',
        'IPL Auction 2024'
    ),
    1
);