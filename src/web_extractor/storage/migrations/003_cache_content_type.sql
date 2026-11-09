-- 003: cache entries remember the Content-Type the server declared (media type
-- only, lowercase). Shown to agents in detailed responses next to the document
-- kind sniffed from the body, so a mislabeled PDF is visible.
ALTER TABLE cache_pages ADD COLUMN content_type TEXT;
