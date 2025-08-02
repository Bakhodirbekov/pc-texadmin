<<<<<<< HEAD
Основная цель бота
Ваш бот — это автоматизированная система для подачи и управления заявками от различных учреждений. Он позволяет пользователям отправлять запросы на обслуживание (например, "не работает компьютер"), а администраторам — управлять базой данных учреждений и просматривать эти заявки.

Функциональные возможности
Бот разделён на две основные части:

1. Для обычных пользователей
Подача заявки: Пользователи могут отправить заявку, последовательно выбрав регион, район и учреждение из списка. Затем они вводят причину заявки и контактные данные.

Статусы заявок: Заявки проходят через несколько понятных статусов, таких как В ожидании, В работе и Завершено.

2. Для администраторов
Управление данными: Администраторы имеют доступ к специальному меню, где они могут добавлять или удалять учреждения.

Добавление учреждений: Процесс добавления нового учреждения также происходит пошагово, через выбор региона и района.

Удаление учреждений: Администраторы могут легко удалить учреждение из базы данных, выбрав его из списка.

Техническая структура и доработки
В основе бота лежат несколько ключевых технологий и решений:

Aiogram и FSM: Для пошагового взаимодействия с пользователем используется конечный автомат (FSM). Это делает процесс подачи заявки или добавления учреждения надёжным и управляемым.

SQLAlchemy и база данных: Вся информация о регионах, районах, учреждениях и заявках хранится в базе данных, что делает систему надёжной.

Клавиатуры: Для удобства навигации используются разные типы клавиатур:

ReplyKeyboardMarkup для основного меню и пошагового ввода данных.

InlineKeyboardMarkup для выбора учреждений, которые нужно удалить.

Мы вместе с вами внесли несколько важных улучшений, чтобы сделать бота ещё лучше:

Надёжная база данных: Мы полностью заполнили базу данных всеми регионами и районами Узбекистана, чтобы вам не пришлось делать это вручную.

Исправленная логика: Мы исправили ошибки в работе FSM и в логике обработки кнопок, чтобы бот всегда вёл себя предсказуемо.

Понятный интерфейс: Мы заменили кнопку "Отмена" на "Главное меню", чтобы навигация стала интуитивно понятной для пользователя.

Таким образом, ваш бот — это готовый инструмент для автоматизации процесса приёма заявок.




admin Pagge 


<img width="550" height="293" alt="image" src="https://github.com/user-attachments/assets/a88a88e1-7ff5-4a57-bac0-9ea46379bea4" />


user page
![photo_2025-07-19_22-54-40](https://github.com/user-attachments/assets/c83365c8-1e2c-4b0c-afec-2045a49928f7)

texpage 

![photo_2025-07-19_22-54-51](https://github.com/user-attachments/assets/1882c070-a0dd-43db-b905-ee24bfc6f726)
=======
# IT Doktor Telegram Boti

Bu loyiha IT muammolarini hal qilish uchun mo'ljallangan Telegram botidir. Foydalanuvchilar o'z muassasalaridagi texnik muammolar bo'yicha arizalar qoldirishlari mumkin, texnik xodimlar va administratorlar esa bu arizalarni boshqarishadi.

---

## ✨ Loyihaning asosiy xususiyatlari

- **Foydalanuvchilar uchun:**
    - Texnik muammo bo'yicha ariza yuborish.
    - O'z arizalarining holatini kuzatish.
    - Profil ma'lumotlarini ko'rish.
- **Texnik xodimlar uchun:**
    - O'ziga tegishli hududdan kelgan yangi arizalarni ko'rish.
    - Arizalar ustida ishlashni boshlash va ularning holatini o'zgartirish.
    - Arizani bajarilgan deb belgilash.
- **Administratorlar uchun:**
    - Barcha hududlardan kelgan arizalarni ko'rish va boshqarish.
    - Foydalanuvchi va texniklarning ro'yxatini ko'rish.
    - Tizim statistikalarini (faol arizalar, bajarilgan arizalar, foydalanuvchilar soni) ko'rish.

---

## 🚀 O'rnatish va ishga tushirish

Loyihani o'rnatish va ishga tushirish uchun quyidagi qadamlarni bajaring.

### 1. Loyiha kodini yuklab olish

Loyiha kodini o'zingizning kompyuteringizga klonlab oling:

```bash
git clone https://github.com/Bakhodirbekov/pc-texadmin
cd https://github.com/Bakhodirbekov/pc-texadmin
>>>>>>> 2a09b2fca53e17fd630203a4b14260599e9804e7
