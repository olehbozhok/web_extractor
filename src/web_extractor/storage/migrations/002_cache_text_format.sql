-- 002: cache entries remember which format their stored text was extracted
-- in (markdown/text). When a later request wants the other format, the
-- pipeline re-extracts from the stored HTML without a network fetch.
ALTER TABLE cache_pages ADD COLUMN text_format TEXT;
