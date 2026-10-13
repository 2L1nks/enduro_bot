SET TIME ZONE 'Europe/Moscow';
INSERT INTO users (tg_id, full_name, role) VALUES (1,'Админ','owner');
INSERT INTO motorcycles (title, model, category, status) VALUES
  ('Kayo T4 #1','Kayo T4','beginner','available'),
  ('Kayo T4 #2','Kayo T4','beginner','available'),
  ('Husqvarna #1','Husqvarna TE 250','intermediate','available'),
  ('KTM 300 #1','KTM 300 EXC','advanced','available'),
  ('KTM 300 #2','KTM 300 EXC','advanced','repair');

-- Выезд 10:00-14:00, буфер 30 мин -> резерв [10:00, 14:30)
INSERT INTO bookings (starts_at, ends_at, status, created_by_id)
VALUES ('2026-11-10 10:00+03','2026-11-10 14:00+03','confirmed',1);
INSERT INTO motorcycle_reservations (motorcycle_id, booking_id, period)
VALUES (1, 1, tstzrange('2026-11-10 10:00+03','2026-11-10 14:30+03','[)'));

\echo '=== 1. Выезд 14:30-18:30 — ровно через буфер, ДОЛЖНО ПРОЙТИ ==='
INSERT INTO bookings (starts_at, ends_at, status, created_by_id)
VALUES ('2026-11-10 14:30+03','2026-11-10 18:30+03','confirmed',1);
INSERT INTO motorcycle_reservations (motorcycle_id, booking_id, period)
VALUES (1, 2, tstzrange('2026-11-10 14:30+03','2026-11-10 19:00+03','[)'));

\echo '=== 2. Выезд 14:00-18:00 — буфер не выдержан, ДОЛЖНО УПАСТЬ ==='
INSERT INTO motorcycle_reservations (motorcycle_id, booking_id, period)
VALUES (2, 2, tstzrange('2026-11-10 14:00+03','2026-11-10 18:30+03','[)'));
SET TIME ZONE 'Europe/Moscow';
\echo '=== 2 (исправлено). Тот же мотоцикл №1, выезд с 14:00 — буфер нарушен, ДОЛЖНО УПАСТЬ ==='
INSERT INTO motorcycle_reservations (motorcycle_id, booking_id, period)
VALUES (1, 2, tstzrange('2026-11-10 14:00+03','2026-11-10 18:30+03','[)'));

\echo '=== 3. Резервы мотоцикла №1 на 10.11 ==='
SELECT motorcycle_id, period FROM motorcycle_reservations WHERE motorcycle_id = 1 ORDER BY period;
-- Плановое ТО Husqvarna 12-14 ноября
INSERT INTO maintenance_periods (motorcycle_id, period, reason)
VALUES (3, tstzrange('2026-11-12 00:00+03','2026-11-15 00:00+03','[)'), 'ТО');

\echo '=== free_by_day: слот с 09:00, выезд 4 ч + буфер 30 мин = 270 мин ==='
WITH days AS (
    SELECT d::date AS day FROM unnest(CAST(ARRAY['2026-11-09','2026-11-10','2026-11-11','2026-11-12','2026-11-13','2026-11-15'] AS date[])) AS d
),
windows AS (
    SELECT day,
           tstzrange(
               (day + CAST('09:00:00' AS time)) AT TIME ZONE 'Europe/Moscow',
               (day + CAST('09:00:00' AS time)) AT TIME ZONE 'Europe/Moscow'
                   + make_interval(mins => 270),
               '[)'
           ) AS w
    FROM days
)
SELECT w.day,
       (SELECT count(*) FROM motorcycles m
        WHERE m.is_active AND m.status = 'available'
          AND NOT EXISTS (SELECT 1 FROM motorcycle_reservations r
              WHERE r.motorcycle_id = m.id AND r.period && w.w)
          AND NOT EXISTS (SELECT 1 FROM maintenance_periods mp
              WHERE mp.motorcycle_id = m.id AND mp.period && w.w)
       ) AS free_count,
       w.w AS window
FROM windows w ORDER BY w.day;
\echo '=== Загрузка парка: часы по времени выезда, без буфера ==='
SELECT m.title,
       count(r.id) AS выездов,
       COALESCE(ROUND((SUM(EXTRACT(epoch FROM b.ends_at) -
                           EXTRACT(epoch FROM b.starts_at))/3600.0)::numeric,1),0) AS часов
FROM motorcycles m
LEFT JOIN motorcycle_reservations r ON r.motorcycle_id = m.id
     AND lower(r.period) >= '2026-11-01+03' AND lower(r.period) < '2026-12-01+03'
LEFT JOIN bookings b ON b.id = r.booking_id
GROUP BY m.id, m.title ORDER BY выездов DESC, m.title;
