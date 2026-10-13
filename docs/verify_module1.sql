\set ON_ERROR_STOP off
-- данные
INSERT INTO users (tg_id, full_name, role) VALUES (1,'Владелец','owner'),(2,'Клиент А','user'),(3,'Клиент Б','user');
INSERT INTO motorcycles (title, model, category) VALUES ('KTM 300 EXC #1','KTM 300 EXC','advanced');
INSERT INTO bookings (starts_at, ends_at, status, created_by_id)
VALUES ('2026-10-20 10:00+03','2026-10-20 14:00+03','confirmed',1),
       ('2026-10-20 13:00+03','2026-10-20 17:00+03','confirmed',1);

\echo '--- 1. Первый резерв 09:30-14:30 (с буфером 30 мин) ---'
INSERT INTO motorcycle_reservations (motorcycle_id, booking_id, period)
VALUES (1, 1, tstzrange('2026-10-20 09:30+03','2026-10-20 14:30+03','[)'));

\echo '--- 2. Выезд с 13:00: мотоцикл ещё занят -> ДОЛЖНО УПАСТЬ ---'
INSERT INTO motorcycle_reservations (motorcycle_id, booking_id, period)
VALUES (1, 2, tstzrange('2026-10-20 12:30+03','2026-10-20 17:30+03','[)'));

\echo '--- 3. Выезд с 15:00 (после буфера): ДОЛЖНО ПРОЙТИ ---'
INSERT INTO motorcycle_reservations (motorcycle_id, booking_id, period)
VALUES (1, 2, tstzrange('2026-10-20 14:30+03','2026-10-20 18:30+03','[)'));

\echo '--- 4. Стык ровно в точке 14:30 не считается пересечением: ---'
SELECT count(*) AS reservations FROM motorcycle_reservations;

\echo '--- 5. Пересекающиеся периоды ТО одного мотоцикла -> второй ДОЛЖЕН УПАСТЬ ---'
INSERT INTO maintenance_periods (motorcycle_id, period, reason)
VALUES (1, tstzrange('2026-11-01 00:00+03','2026-11-05 00:00+03','[)'), 'ТО');
INSERT INTO maintenance_periods (motorcycle_id, period, reason)
VALUES (1, tstzrange('2026-11-03 00:00+03','2026-11-08 00:00+03','[)'), 'замена поршневой');

\echo '--- 6. Невалидные данные отсекаются CHECK-ами ---'
INSERT INTO users (tg_id, full_name, height_cm) VALUES (99,'Гулливер', 300);
INSERT INTO bookings (starts_at, ends_at) VALUES ('2026-10-21 14:00+03','2026-10-21 10:00+03');
