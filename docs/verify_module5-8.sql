SET TIME ZONE 'Europe/Moscow';
INSERT INTO users (tg_id, full_name, role, height_cm, weight_kg, shoe_size, experience) VALUES
 (1,'Владелец','owner',NULL,NULL,NULL,NULL),
 (2,'Иван Петров','user',178,75,43,'intermediate'),
 (3,'Пётр Сидоров','user',190,95,45,'advanced'),
 (4,'Анна Коваль','user',165,55,38,'beginner');
INSERT INTO motorcycles (title,model,category,status,max_rider_height_cm,max_rider_weight_kg) VALUES
 ('Kayo T4 #1','Kayo T4','beginner','available',175,90),
 ('Husqvarna #1','Husqvarna TE 250','intermediate','available',NULL,100),
 ('KTM 300 #1','KTM 300 EXC','advanced','available',NULL,NULL);

-- Подтверждённый выезд завтра
INSERT INTO bookings (id,created_by_id,starts_at,ends_at,status,requested_seats,meeting_point,total_price)
VALUES (1,1,now()+interval '1 day 3 hours', now()+interval '1 day 7 hours','confirmed',3,'База, Пушкино',15000);
INSERT INTO booking_participants (id,booking_id,user_id,motorcycle_id,height_cm,weight_kg,shoe_size,experience,price) VALUES
 (1,1,2,2,178,75,43,'intermediate',5000),
 (2,1,3,3,190,95,45,'advanced',5000),
 (3,1,4,1,165,55,38,'beginner',5000);
INSERT INTO motorcycle_reservations (motorcycle_id,booking_id,participant_id,period) VALUES
 (2,1,1,tstzrange(now()+interval '1 day 3 hours', now()+interval '1 day 7 hours 30 min','[)')),
 (3,1,2,tstzrange(now()+interval '1 day 3 hours', now()+interval '1 day 7 hours 30 min','[)')),
 (1,1,3,tstzrange(now()+interval '1 day 3 hours', now()+interval '1 day 7 hours 30 min','[)'));

\echo '=== 1. Постановка напоминаний: 24ч уже прошло, 2ч в будущем ==='
-- Выезд через 27 часов: напоминание за 24ч ещё впереди, ставим оба
INSERT INTO notifications (user_id,booking_id,kind,status,send_at,payload)
SELECT p.user_id, 1, 'reminder_24h','scheduled',
       (SELECT starts_at FROM bookings WHERE id=1) - interval '24 hours',
       jsonb_build_object('starts_at',(SELECT starts_at::text FROM bookings WHERE id=1),'meeting_point','База, Пушкино')
FROM booking_participants p WHERE p.booking_id=1;
INSERT INTO notifications (user_id,booking_id,kind,status,send_at,payload)
SELECT p.user_id, 1, 'reminder_2h','scheduled',
       (SELECT starts_at FROM bookings WHERE id=1) - interval '2 hours', '{}'::jsonb
FROM booking_participants p WHERE p.booking_id=1;
SELECT kind, count(*), min(send_at)::timestamp(0) AS когда FROM notifications GROUP BY kind ORDER BY kind;

\echo '=== 2. Запрос воркера: что отправлять прямо сейчас (ничего) ==='
SELECT count(*) AS к_отправке FROM notifications
WHERE status='scheduled' AND send_at <= now() AND attempts < 3;

\echo '=== 3. Сдвигаем время: напоминание за 24ч стало актуальным ==='
UPDATE notifications SET send_at = now() - interval '1 minute' WHERE kind='reminder_24h';
SELECT count(*) AS к_отправке FROM notifications
WHERE status='scheduled' AND send_at <= now() AND attempts < 3;

\echo '=== 4. Отмена записи снимает только неотправленные ==='
UPDATE notifications SET status='sent', sent_at=now() WHERE kind='reminder_24h' AND user_id=2;
UPDATE notifications SET status='cancelled' WHERE booking_id=1 AND status='scheduled';
SELECT status, count(*) FROM notifications GROUP BY status ORDER BY status;
SET TIME ZONE 'Europe/Moscow';
-- Завершаем выезд и отмечаем оплату двоих из трёх
UPDATE bookings SET status='completed' WHERE id=1;
INSERT INTO payments (booking_id,user_id,amount,method,status,paid_at) VALUES
 (1,2,5000,'cash','paid',now()),(1,3,5000,'cash','paid',now());

\echo '=== Выручка за месяц ==='
SELECT
 (SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='paid'
    AND paid_at >= date_trunc('month',now())) AS получено,
 (SELECT COALESCE(SUM(total_price),0) FROM bookings
    WHERE status IN ('confirmed','completed') AND starts_at >= date_trunc('month',now())) AS ожидается,
 (SELECT count(*) FROM bookings WHERE status='completed'
    AND starts_at >= date_trunc('month',now())) AS выездов,
 (SELECT count(DISTINCT p.user_id) FROM booking_participants p
    JOIN bookings b ON b.id=p.booking_id WHERE b.starts_at >= date_trunc('month',now())) AS клиентов;

\echo '=== Топ клиентов (поездки и потрачено) ==='
WITH paid AS (SELECT user_id, SUM(amount) t FROM payments WHERE status='paid' GROUP BY user_id)
SELECT u.full_name, count(p.id) AS поездок, COALESCE(paid.t,0) AS потрачено
FROM users u
JOIN booking_participants p ON p.user_id=u.id
JOIN bookings b ON b.id=p.booking_id AND b.status='completed'
LEFT JOIN paid ON paid.user_id=u.id
GROUP BY u.id, u.full_name, paid.t ORDER BY поездок DESC, u.full_name;

\echo '=== Статистика пользователя Иван Петров ==='
SELECT
 (SELECT count(*) FROM booking_participants p JOIN bookings b ON b.id=p.booking_id
   WHERE p.user_id=2 AND b.status='completed') AS визитов,
 (SELECT COALESCE(SUM(EXTRACT(epoch FROM b.ends_at)-EXTRACT(epoch FROM b.starts_at))/60,0)::int
   FROM booking_participants p JOIN bookings b ON b.id=p.booking_id
   WHERE p.user_id=2 AND b.status='completed') AS минут,
 (SELECT COALESCE(SUM(amount),0) FROM payments WHERE user_id=2 AND status='paid') AS потрачено;

\echo '=== Экспорт: одна строка на участника ==='
SELECT b.id, b.starts_at::date AS дата, u.full_name AS клиент,
       p.height_cm AS рост, p.weight_kg AS вес, m.title AS мотоцикл, p.price AS цена
FROM bookings b
JOIN booking_participants p ON p.booking_id=b.id
JOIN users u ON u.id=p.user_id
LEFT JOIN motorcycles m ON m.id=p.motorcycle_id
ORDER BY b.starts_at DESC;

\echo '=== Подбор: кто на какой технике (проверка ограничений) ==='
SELECT u.full_name, u.height_cm AS рост, u.weight_kg AS вес, m.title,
       CASE WHEN (m.max_rider_height_cm IS NULL OR u.height_cm <= m.max_rider_height_cm)
             AND (m.max_rider_weight_kg IS NULL OR u.weight_kg <= m.max_rider_weight_kg)
            THEN 'подходит' ELSE 'НЕ ПОДХОДИТ' END AS проверка
FROM booking_participants p
JOIN users u ON u.id=p.user_id JOIN motorcycles m ON m.id=p.motorcycle_id
WHERE p.booking_id=1 ORDER BY u.full_name;
