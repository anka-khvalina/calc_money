-- Проставить «не дерби» всем существующим матчам.
-- Выполнить в Supabase SQL Editor (нужны права на UPDATE matches).

UPDATE matches
SET derby_weight = 0
WHERE derby_weight IS NULL
   OR derby_weight <> 0;

-- Опционально: default для новых строк (если колонка без default)
-- ALTER TABLE matches ALTER COLUMN derby_weight SET DEFAULT 0;
