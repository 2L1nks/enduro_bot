@echo off
rem Запуск бота на Windows с автоперезапуском после сбоя.
rem Двойной щелчок — бот запущен. Закрыть окно — бот остановлен.
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Не найдено окружение .venv. Сначала выполните установку из README, пункт 2.5.
    pause
    exit /b 1
)
if not exist ".env" (
    echo Не найден файл .env. Скопируйте .env.example в .env и заполните, README пункт 2.6.
    pause
    exit /b 1
)

:loop
".venv\Scripts\python.exe" -m app.main
echo.
echo Бот остановился. Перезапуск через 10 секунд, чтобы выйти - закройте окно.
timeout /t 10 /nobreak >nul
goto loop
