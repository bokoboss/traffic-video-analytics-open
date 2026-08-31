ALTER TABLE crossing_event_ledger ADD COLUMN side_a_label TEXT NOT NULL DEFAULT 'Side A';
ALTER TABLE crossing_event_ledger ADD COLUMN side_b_label TEXT NOT NULL DEFAULT 'Side B';
ALTER TABLE crossing_event_ledger ADD COLUMN readable_direction_label TEXT NOT NULL DEFAULT '';
