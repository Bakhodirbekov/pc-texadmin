#!/usr/bin/env python3
"""
Telegram bot dlya upravleniya zayavkami
Kompleksnyy bot dlya upravleniya institutsionalnymi zayavkami s rolyami administratora, tekhnika i polzovatelya
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List
import os
from dataclasses import dataclass

# Third-party imports
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.orm import sessionmaker, Session, relationship, declarative_base
from sqlalchemy.sql import func
import pandas as pd
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors


# Konfiguratsiya
@dataclass
class Config:
    BOT_TOKEN: str = "7147789967:AAH0GgOzPU_CtX3p_tCu9szc28BUPUqsqI4"
    ADMIN_IDS: List[int] = None
    DATABASE_URL: str = "sqlite:///requests.db"
    REPORTS_DIR: str = "reports"

    def __post_init__(self):
        if self.ADMIN_IDS is None:
            self.ADMIN_IDS = [584323689]  # Zamenite na realnyy ID administratora

        # Sozdaniye papki dlya otchetov, yesli ona ne sushchestvuyet
        os.makedirs(self.REPORTS_DIR, exist_ok=True)


config = Config()

# Modeli baz dannykh
Base = declarative_base()


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    region = Column(String(100), nullable=False)
    district = Column(String(100), nullable=False)
    institution = Column(String(200), nullable=False)
    full_name = Column(String(200), nullable=False)
    position = Column(String(200), nullable=False)
    role = Column(String(20), default='user')  # user, technician, admin
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())

    requests = relationship("Request", back_populates="user", foreign_keys="[Request.user_id]")


class Request(Base):
    __tablename__ = 'requests'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    region = Column(String(100), nullable=False)
    district = Column(String(100), nullable=False)
    institution = Column(String(200), nullable=False)
    reason = Column(Text, nullable=False)
    floor_room = Column(String(100), nullable=False)
    submitted_by = Column(String(200), nullable=False)
    status = Column(String(20), default='pending')  # pending, in_progress, completed, not_completed
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="requests", foreign_keys=[user_id])


class Region(Base):
    __tablename__ = 'regions'

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)


class District(Base):
    __tablename__ = 'districts'

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    region_id = Column(Integer, ForeignKey('regions.id'), nullable=False)
    is_active = Column(Boolean, default=True)

    region = relationship("Region")


class Institution(Base):
    __tablename__ = 'institutions'

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    district_id = Column(Integer, ForeignKey('districts.id'), nullable=False)
    is_active = Column(Boolean, default=True)

    district = relationship("District")


# Nastrojka baz dannykh
engine = create_engine(config.DATABASE_URL, echo=False)
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Sostoyaniya
class UserRegistration(StatesGroup):
    waiting_for_region = State()
    waiting_for_district = State()
    waiting_for_institution = State()
    waiting_for_full_name = State()
    waiting_for_position = State()


class RequestSubmission(StatesGroup):
    waiting_for_region = State()
    waiting_for_district = State()
    waiting_for_institution = State()
    waiting_for_reason = State()
    waiting_for_floor_room = State()
    waiting_for_submitted_by = State()
    waiting_for_confirmation = State()


class TechnicianRegistration(StatesGroup):
    waiting_for_region = State()
    waiting_for_district = State()
    waiting_for_institution = State()
    waiting_for_full_name = State()
    waiting_for_position = State()


class AdminAddTechnician(StatesGroup):
    waiting_for_telegram_id = State()
    waiting_for_region = State()
    waiting_for_district = State()
    waiting_for_institution = State()
    waiting_for_full_name = State()


class AdminManageData(StatesGroup):
    add_institution_waiting_for_region = State()
    add_institution_waiting_for_district = State()
    add_institution_waiting_for_name = State()
    delete_institution_waiting_for_confirmation = State()


# Vspomogatelnyye funktsii dlya raboty s bazoy dannykh
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user_by_telegram_id(db: Session, telegram_id: int) -> Optional[User]:
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def create_user(db: Session, telegram_id: int, region: str, district: str,
                institution: str, full_name: str, position: str, role: str = 'user') -> User:
    user = User(
        telegram_id=telegram_id,
        region=region,
        district=district,
        institution=institution,
        full_name=full_name,
        position=position,
        role=role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_regions(db: Session) -> List[Region]:
    return db.query(Region).filter(Region.is_active == True).all()


def get_districts_by_region(db: Session, region_name: str) -> List[District]:
    return db.query(District).join(Region).filter(
        Region.name == region_name,
        District.is_active == True
    ).all()


def get_institutions_by_district(db: Session, district_name: str) -> List[Institution]:
    return db.query(Institution).join(District).filter(
        District.name == district_name,
        Institution.is_active == True
    ).all()


# Generatory klavish
def create_regions_keyboard() -> ReplyKeyboardMarkup:
    db = SessionLocal()
    regions = get_regions(db)
    db.close()

    buttons = []
    for region in regions:
        buttons.append([KeyboardButton(text=region.name)])
    buttons.append([KeyboardButton(text="Отмена")])

    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def create_districts_keyboard(region_name: str) -> ReplyKeyboardMarkup:
    db = SessionLocal()
    districts = get_districts_by_region(db, region_name)
    db.close()

    buttons = []
    for district in districts:
        buttons.append([KeyboardButton(text=district.name)])
    buttons.append([KeyboardButton(text="Отмена")])

    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def create_institutions_keyboard(district_name: str) -> ReplyKeyboardMarkup:
    db = SessionLocal()
    institutions = get_institutions_by_district(db, district_name)
    db.close()

    buttons = []
    for institution in institutions:
        buttons.append([KeyboardButton(text=institution.name)])
    buttons.append([KeyboardButton(text="Отмена")])

    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def create_main_user_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📝 Отправить заявку")],
        [KeyboardButton(text="📋 Мои заявки")],
        [KeyboardButton(text="ℹ️ Профиль")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def create_technician_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="🔧 Просмотреть заявки")],
        [KeyboardButton(text="📊 Моя статистика")],
        [KeyboardButton(text="ℹ️ Профиль")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def create_admin_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📋 Просмотр заявок"), KeyboardButton(text="👥 Управление пользователями")],
        [KeyboardButton(text="🏢 Управление данными"), KeyboardButton(text="📊 Отчеты")],
        [KeyboardButton(text="🔧 Управление техниками")],
        [KeyboardButton(text="ℹ️ Профиль")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def create_request_status_keyboard(request_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"status_completed_{request_id}")],
        [InlineKeyboardButton(text="🔄 В процессе", callback_data=f"status_in_progress_{request_id}")],
        [InlineKeyboardButton(text="❌ Не выполнено", callback_data=f"status_not_completed_{request_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_confirmation_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✅ Да", callback_data="confirm_yes")],
        [InlineKeyboardButton(text="❌ Нет", callback_data="confirm_no")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_admin_manage_technicians_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить техника", callback_data="admin_add_tech")],
        [InlineKeyboardButton(text="❌ Удалить техника", callback_data="admin_delete_tech")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_delete_technician_keyboard(technicians: List[User]) -> InlineKeyboardMarkup:
    buttons = []
    for technician in technicians:
        buttons.append([InlineKeyboardButton(text=technician.full_name, callback_data=f"delete_tech_{technician.id}")])

    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="cancel_delete")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_admin_manage_data_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить учреждение", callback_data="add_institution")],
        [InlineKeyboardButton(text="❌ Удалить учреждение", callback_data="delete_institution")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_admin_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_delete_institution_keyboard(institutions: List[Institution]) -> InlineKeyboardMarkup:
    buttons = []
    for institution in institutions:
        buttons.append([InlineKeyboardButton(text=institution.name, callback_data=f"delete_inst_{institution.id}")])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_manage_data")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Generator otchetov v formate PDF
class PDFReportGenerator:
    def __init__(self, db: Session):
        self.db = db
        self.styles = getSampleStyleSheet()

    def generate_weekly_report(self, start_date: datetime, end_date: datetime) -> str:
        """Generatsiya yezhenedelnogo PDF-otcheta"""
        filename = f"weekly_report_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.pdf"
        filepath = os.path.join(config.REPORTS_DIR, filename)

        # Sozdaniye PDF-dokumenta
        doc = SimpleDocTemplate(filepath, pagesize=A4)
        story = []

        # Zagolovok
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=18,
            spaceAfter=30,
            alignment=1  # Vyravnivaniye po tsentru
        )
        story.append(Paragraph(f"Yezhenedelnyy otchet ({start_date.strftime('%Y-%m-%d')} po {end_date.strftime('%Y-%m-%d')})",
                               title_style))
        story.append(Spacer(1, 20))

        # Polucheniye dannykh o zayavkakh
        requests = self.db.query(Request).filter(
            Request.created_at >= start_date,
            Request.created_at <= end_date
        ).all()

        # Obobshchennaya statistika
        total_requests = len(requests)
        completed_requests = sum(1 for r in requests if r.status == 'completed')
        in_progress_requests = sum(1 for r in requests if r.status == 'in_progress')
        pending_requests = sum(1 for r in requests if r.status == 'pending')
        not_completed_requests = sum(1 for r in requests if r.status == 'not_completed')

        # Svobodnaya tablitsa
        summary_data = [
            ['Status', 'Kolichestvo', 'Protsent'],
            ['Vsego zayavok', str(total_requests), '100%'],
            ['Vypolneno', str(completed_requests),
             f'{completed_requests / total_requests * 100:.1f}%' if total_requests > 0 else '0%'],
            ['V protsesse', str(in_progress_requests),
             f'{in_progress_requests / total_requests * 100:.1f}%' if total_requests > 0 else '0%'],
            ['V ozhidanii', str(pending_requests),
             f'{pending_requests / total_requests * 100:.1f}%' if total_requests > 0 else '0%'],
            ['Ne vypolneno', str(not_completed_requests),
             f'{not_completed_requests / total_requests * 100:.1f}%' if total_requests > 0 else '0%']
        ]

        summary_table = Table(summary_data)
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))

        story.append(summary_table)
        story.append(Spacer(100, 30))

        # Podrobnaya tablitsa zayavok
        if requests:
            story.append(Paragraph("Podrobnyye zayavki", self.styles['Heading2']))
            story.append(Spacer(1, 12))

            # Preobrazovaniye zayavok v DataFrame dlya oblegcheniya manipulyatsiy
            df_data = []
            for req in requests:
                df_data.append({
                    'ID': req.id,
                    'Polzovatel': req.user.full_name,
                    'Region': req.region,
                    'Rayon': req.district,
                    'Uchrezhdeniye': req.institution,
                    'Prichina': req.reason[:50] + "..." if len(req.reason) > 50 else req.reason,
                    'Status': req.status.title(),
                    'Sozdano': req.created_at.strftime('%Y-%m-%d %H:%M')
                })

            df = pd.DataFrame(df_data)

            # Sozdaniye dannykh tablitsy
            table_data = [df.columns.tolist()]
            for _, row in df.iterrows():
                table_data.append(row.tolist())

            # Sozdaniye tablitsy
            detailed_table = Table(table_data)
            detailed_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('FONTSIZE', (0, 1), (-1, -1), 8)
            ]))

            story.append(detailed_table)

        # Postroyeniye PDF
        doc.build(story)
        return filepath


# Initsializatsiya bota
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()


# Initsializatsiya primernykh dannykh
def initialize_sample_data():
    db = SessionLocal()

    # Proverka sushchestvovaniya dannykh
    if db.query(Region).first():
        db.close()
        return

    # Sozdaniye primernykh regionov
    regions = [
        Region(name="Ташкент"),
        Region(name="Ташкентская область"),
        Region(name="Самаркандская область"),
        Region(name="Бухарская область")
    ]

    for region in regions:
        db.add(region)
    db.commit()

    # Sozdaniye primernykh rayonov
    districts = [
        District(name="Мирабадский район", region_id=1),
        District(name="Юнусабадский район", region_id=1),
        District(name="Шайхантахурский район", region_id=1),
        District(name="Чиланзарский район", region_id=1),
        District(name="Юкоричирчикский район", region_id=2),
        District(name="Пайарикский район", region_id=3)
    ]

    for district in districts:
        db.add(district)
    db.commit()

    # Sozdaniye primernykh uchrezhdeniy
    institutions = [
        Institution(name="1-я Семейная поликлиника", district_id=1),
        Institution(name="2-я Семейная поликлиника", district_id=1),
        Institution(name="Городская больница №1", district_id=2),
        Institution(name="Городская больница №2", district_id=2),
        Institution(name="Министерство образования", district_id=3),
        Institution(name="Университет", district_id=4),
        Institution(name="Детский сад №5", district_id=5),
        Institution(name="Школа №10", district_id=6)
    ]

    for institution in institutions:
        db.add(institution)
    db.commit()

    db.close()


# Obrabotchiki
@router.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)

    if user:
        if user.role == 'admin':
            await message.answer("С возвращением, Администратор! 👋", reply_markup=create_admin_keyboard())
        elif user.role == 'technician':
            await message.answer("С возвращением, Техник! 👋", reply_markup=create_technician_keyboard())
        else:
            await message.answer("С возвращением! 👋", reply_markup=create_main_user_keyboard())
    else:
        await message.answer(
            "Добро пожаловать! 👋 Давайте начнем вашу регистрацию.\n\n"
            "Пожалуйста, выберите ваш регион:",
            reply_markup=create_regions_keyboard()
        )
        await state.set_state(UserRegistration.waiting_for_region)

    db.close()


@router.message(Command("texstart"))
async def technician_start_handler(message: Message, state: FSMContext):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)

    if user:
        if user.role == 'technician':
            await message.answer("С возвращением, Техник! 👋", reply_markup=create_technician_keyboard())
        else:
            await message.answer("Вы уже зарегистрированы как другой тип пользователя.")
    else:
        await message.answer(
            "Регистрация техника 🔧\n\n"
            "Пожалуйста, выберите ваш регион:",
            reply_markup=create_regions_keyboard()
        )
        await state.set_state(TechnicianRegistration.waiting_for_region)

    db.close()


@router.message(Command("adminstart"))
async def admin_start_handler(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("❌ У вас нет разрешения на доступ к панели администратора.")
        return

    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)

    if not user:
        user = create_user(
            db, message.from_user.id, "Админ", "Админ", "Админ",
            message.from_user.full_name or "Администратор", "Администратор", "admin"
        )
    elif user.role != 'admin':
        user.role = 'admin'
        db.commit()

    await message.answer("Добро пожаловать, Администратор! 👋", reply_markup=create_admin_keyboard())
    db.close()


@router.message(Command("report"))
async def generate_report_handler(message: Message):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)

    if not user or user.role != 'admin':
        await message.answer("❌ У вас нет разрешения на генерацию отчетов.")
        db.close()
        return

    await message.answer("📊 Генерация еженедельного отчета...")

    today = datetime.now()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    report_generator = PDFReportGenerator(db)
    filepath = report_generator.generate_weekly_report(start_of_week, end_of_week)

    try:
        document = FSInputFile(filepath)
        await message.answer_document(document, caption="📊 Еженедельный отчет")
    except Exception as e:
        await message.answer(f"❌ Ошибка при генерации отчета: {str(e)}")

    db.close()


# Sostoyaniya registratsii polzovatelya
@router.message(StateFilter(UserRegistration.waiting_for_region), F.text)
async def process_user_region(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/start")]], resize_keyboard=True))
        return

    await state.update_data(region=message.text)
    await message.answer(
        f"Выбранный регион: {message.text}\n\n"
        "Теперь, пожалуйста, выберите ваш район:",
        reply_markup=create_districts_keyboard(message.text)
    )
    await state.set_state(UserRegistration.waiting_for_district)


@router.message(StateFilter(UserRegistration.waiting_for_district), F.text)
async def process_user_district(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/start")]], resize_keyboard=True))
        return

    await state.update_data(district=message.text)
    await message.answer(
        f"Выбранный район: {message.text}\n\n"
        "Теперь, пожалуйста, выберите ваше учреждение:",
        reply_markup=create_institutions_keyboard(message.text)
    )
    await state.set_state(UserRegistration.waiting_for_institution)


@router.message(StateFilter(UserRegistration.waiting_for_institution), F.text)
async def process_user_institution(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/start")]], resize_keyboard=True))
        return

    await state.update_data(institution=message.text)
    await message.answer(
        f"Выбранное учреждение: {message.text}\n\n"
        "Пожалуйста, введите ваше полное имя:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(UserRegistration.waiting_for_full_name)


@router.message(StateFilter(UserRegistration.waiting_for_full_name), F.text)
async def process_user_full_name(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/start")]], resize_keyboard=True))
        return

    await state.update_data(full_name=message.text)
    await message.answer(
        f"Полное имя: {message.text}\n\n"
        "Пожалуйста, введите вашу должность:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(UserRegistration.waiting_for_position)


@router.message(StateFilter(UserRegistration.waiting_for_position), F.text)
async def process_user_position(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/start")]], resize_keyboard=True))
        return

    data = await state.get_data()

    db = SessionLocal()
    user = create_user(
        db, message.from_user.id, data['region'], data['district'],
        data['institution'], data['full_name'], message.text
    )
    db.close()

    await state.clear()
    await message.answer(
        "✅ Регистрация успешно завершена!\n\n"
        f"Регион: {data['region']}\n"
        f"Район: {data['district']}\n"
        f"Учреждение: {data['institution']}\n"
        f"Полное имя: {data['full_name']}\n"
        f"Должность: {message.text}\n\n"
        "Добро пожаловать в систему! 🎉",
        reply_markup=create_main_user_keyboard()
    )


# Sostoyaniya registratsii tekhnika (analogichno registratsii polzovatelya)
@router.message(StateFilter(TechnicianRegistration.waiting_for_region), F.text)
async def process_technician_region(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/texstart")]], resize_keyboard=True))
        return

    await state.update_data(region=message.text)
    await message.answer(
        f"Выбранный регион: {message.text}\n\n"
        "Теперь, пожалуйста, выберите ваш район:",
        reply_markup=create_districts_keyboard(message.text)
    )
    await state.set_state(TechnicianRegistration.waiting_for_district)


@router.message(StateFilter(TechnicianRegistration.waiting_for_district), F.text)
async def process_technician_district(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/texstart")]], resize_keyboard=True))
        return

    await state.update_data(district=message.text)
    await message.answer(
        f"Выбранный район: {message.text}\n\n"
        "Теперь, пожалуйста, выберите ваше учреждение:",
        reply_markup=create_institutions_keyboard(message.text)
    )
    await state.set_state(TechnicianRegistration.waiting_for_institution)


@router.message(StateFilter(TechnicianRegistration.waiting_for_institution), F.text)
async def process_technician_institution(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/texstart")]], resize_keyboard=True))
        return

    await state.update_data(institution=message.text)
    await message.answer(
        f"Выбранное учреждение: {message.text}\n\n"
        "Пожалуйста, введите ваше полное имя:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(TechnicianRegistration.waiting_for_full_name)


@router.message(StateFilter(TechnicianRegistration.waiting_for_full_name), F.text)
async def process_technician_full_name(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/texstart")]], resize_keyboard=True))
        return

    await state.update_data(full_name=message.text)
    await message.answer(
        f"Полное имя: {message.text}\n\n"
        "Пожалуйста, введите вашу должность:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(TechnicianRegistration.waiting_for_position)


@router.message(StateFilter(TechnicianRegistration.waiting_for_position), F.text)
async def process_technician_position(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/texstart")]], resize_keyboard=True))
        return

    data = await state.get_data()

    db = SessionLocal()
    user = create_user(
        db, message.from_user.id, data['region'], data['district'],
        data['institution'], data['full_name'], message.text, 'technician'
    )
    db.close()

    await state.clear()
    await message.answer(
        "✅ Регистрация техника завершена!\n\n"
        f"Регион: {data['region']}\n"
        f"Район: {data['district']}\n"
        f"Учреждение: {data['institution']}\n"
        f"Полное имя: {data['full_name']}\n"
        f"Должность: {message.text}\n\n"
        "Добро пожаловать в систему! 🔧",
        reply_markup=create_technician_keyboard()
    )

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🔧 Зарегистрирован новый техник:\n"
                f"Имя: {data['full_name']}\n"
                f"Должность: {message.text}\n"
                f"Регион: {data['region']}\n"
                f"Район: {data['district']}\n"
                f"Учреждение: {data['institution']}"
            )
        except:
            pass


# Sostoyaniya otpravki zayavki
@router.message(F.text == "📝 Отправить заявку")
async def submit_request_handler(message: Message, state: FSMContext):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)
    if not user:
        await message.answer("Пожалуйста, сначала зарегистрируйтесь используя /start.")
        db.close()
        return

    await message.answer(
        "📋 Давайте отправим новую заявку.\n\n"
        "Пожалуйста, выберите регион:",
        reply_markup=create_regions_keyboard()
    )
    await state.set_state(RequestSubmission.waiting_for_region)
    db.close()


@router.message(StateFilter(RequestSubmission.waiting_for_region), F.text)
async def process_request_region(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Отправка заявки отменена.", reply_markup=create_main_user_keyboard())
        return

    await state.update_data(region=message.text)
    await message.answer(
        f"Выбранный регион: {message.text}\n\n"
        "Теперь, пожалуйста, выберите район:",
        reply_markup=create_districts_keyboard(message.text)
    )
    await state.set_state(RequestSubmission.waiting_for_district)


@router.message(StateFilter(RequestSubmission.waiting_for_district), F.text)
async def process_request_district(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Отправка заявки отменена.", reply_markup=create_main_user_keyboard())
        return

    await state.update_data(district=message.text)
    await message.answer(
        f"Выбранный район: {message.text}\n\n"
        "Теперь, пожалуйста, выберите ваше учреждение:",
        reply_markup=create_institutions_keyboard(message.text)
    )
    await state.set_state(RequestSubmission.waiting_for_institution)


@router.message(StateFilter(RequestSubmission.waiting_for_institution), F.text)
async def process_request_institution(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Отправка заявки отменена.", reply_markup=create_main_user_keyboard())
        return

    await state.update_data(institution=message.text)
    await message.answer(
        f"Выбранное учреждение: {message.text}\n\n"
        "Пожалуйста, введите причину заявки:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(RequestSubmission.waiting_for_reason)


@router.message(StateFilter(RequestSubmission.waiting_for_reason), F.text)
async def process_request_reason(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Отправка заявки отменена.", reply_markup=create_main_user_keyboard())
        return

    await state.update_data(reason=message.text)
    await message.answer(
        f"Причина: {message.text}\n\n"
        "Пожалуйста, укажите этаж и номер комнаты (например, '2 этаж, комната 78'):",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(RequestSubmission.waiting_for_floor_room)


@router.message(StateFilter(RequestSubmission.waiting_for_floor_room), F.text)
async def process_request_floor_room(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Отправка заявки отменена.", reply_markup=create_main_user_keyboard())
        return

    await state.update_data(floor_room=message.text)
    await message.answer(
        f"Этаж и комната: {message.text}\n\n"
        "Пожалуйста, введите имя человека, отправляющего эту заявку:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(RequestSubmission.waiting_for_submitted_by)


@router.message(StateFilter(RequestSubmission.waiting_for_submitted_by), F.text)
async def process_request_submitted_by(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Отправка заявки отменена.", reply_markup=create_main_user_keyboard())
        return

    data = await state.get_data()
    await state.update_data(submitted_by=message.text)

    confirmation_text = (
        "📋 Пожалуйста, подтвердите детали вашей заявки:\n\n"
        f"🌍 Регион: {data['region']}\n"
        f"🏘️ Район: {data['district']}\n"
        f"🏢 Учреждение: {data['institution']}\n"
        f"📝 Причина: {data['reason']}\n"
        f"📍 Этаж и комната: {data['floor_room']}\n"
        f"👤 Отправил: {message.text}\n"
        f"📅 Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        "Вы проверили детали?"
    )

    await message.answer(
        confirmation_text,
        reply_markup=create_confirmation_keyboard()
    )
    await state.set_state(RequestSubmission.waiting_for_confirmation)


@router.callback_query(StateFilter(RequestSubmission.waiting_for_confirmation))
async def process_request_confirmation(callback: CallbackQuery, state: FSMContext):
    if callback.data == "confirm_yes":
        data = await state.get_data()
        db = SessionLocal()

        user = get_user_by_telegram_id(db, callback.from_user.id)

        request = Request(
            user_id=user.id,
            region=data['region'],
            district=data['district'],
            institution=data['institution'],
            reason=data['reason'],
            floor_room=data['floor_room'],
            submitted_by=data['submitted_by']
        )

        db.add(request)
        db.commit()
        db.refresh(request)

        await callback.message.edit_text(
            "✅ Заявка успешно отправлена!\n\n"
            f"ID заявки: #{request.id}\n"
            f"Статус: В ожидании\n\n"
            "Ваша заявка была отправлена администраторам и техникам.",
            reply_markup=None
        )

        admins = db.query(User).filter(User.role == 'admin').all()
        for admin in admins:
            try:
                await bot.send_message(
                    admin.telegram_id,
                    f"🔔 Новая заявка #{request.id}\n\n"
                    f"👤 Пользователь: {user.full_name}\n"
                    f"🌍 Регион: {request.region}\n"
                    f"🏘️ Район: {request.district}\n"
                    f"🏢 Учреждение: {request.institution}\n"
                    f"📝 Причина: {request.reason}\n"
                    f"📍 Этаж и комната: {request.floor_room}\n"
                    f"📅 Дата: {request.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                )
            except Exception as e:
                logging.error(f"Не удалось отправить сообщение администратору {admin.telegram_id}: {e}")

        technicians = db.query(User).filter(
            User.role == 'technician',
            User.region == request.region,
            User.district == request.district,
            User.institution == request.institution
        ).all()
        for technician in technicians:
            try:
                await bot.send_message(
                    technician.telegram_id,
                    f"🔔 Вам поступила новая заявка:\n\n"
                    f"**ID:** #{request.id}\n"
                    f"**Uchrezhdeniye:** {request.institution}\n"
                    f"**Prichina:** {request.reason}\n"
                    f"**Etazh/Komnata:** {request.floor_room}\n"
                    f"**Status:** В ожидании\n"
                )
            except Exception as e:
                logging.error(f"Не удалось отправить сообщение технику {technician.telegram_id}: {e}")

        db.close()
        await state.clear()
    else:
        await callback.message.edit_text("❌ Отправка заявки отменена.", reply_markup=None)
        await state.clear()


# Obrabotchiki dlya tekhnikov
@router.message(F.text == "🔧 Просмотреть заявки")
async def view_technician_requests_handler(message: Message):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)
    if not user or user.role != 'technician':
        await message.answer("❌ У вас нет разрешения на это действие.")
        db.close()
        return

    requests = db.query(Request).filter(
        Request.institution == user.institution,
        Request.status.in_(['pending', 'in_progress'])
    ).order_by(Request.created_at.desc()).all()

    if not requests:
        await message.answer("В вашем учреждении нет активных заявок.")
    else:
        for req in requests:
            response_text = (
                f"🔧 **Активная заявка:**\n\n"
                f"**ID:** #{req.id}\n"
                f"**Причина:** {req.reason}\n"
                f"**Этаж/Комната:** {req.floor_room}\n"
                f"**Статус:** {req.status.title()}\n"
                f"**Отправил:** {req.submitted_by}\n"
                f"**Дата:** {req.created_at.strftime('%Y-%m-%d %H:%M')}\n"
            )
            await message.answer(
                response_text,
                reply_markup=create_request_status_keyboard(req.id)
            )

    db.close()


@router.callback_query(F.data.startswith("status_"))
async def update_request_status(callback: CallbackQuery):
    try:
        parts = callback.data.split('_')
        new_status = parts[1]
        request_id = int(parts[2])

        db = SessionLocal()
        request = db.query(Request).get(request_id)
        technician = get_user_by_telegram_id(db, callback.from_user.id)

        if not request or request.institution != technician.institution:
            await callback.answer("❌ У вас нет разрешения изменять статус этой заявки.", show_alert=True)
            db.close()
            return

        request.status = new_status
        db.commit()
        db.refresh(request)

        await callback.message.edit_text(
            f"✅ Статус заявки #{request_id} обновлен на: **{new_status.title()}**",
            reply_markup=None
        )

        user_who_submitted = db.query(User).get(request.user_id)
        if user_who_submitted:
            await bot.send_message(
                user_who_submitted.telegram_id,
                f"🔔 Обновление вашей заявки #{request.id}:\n\n"
                f"Статус был обновлен до: **{new_status.title()}**.\n\n"
                f"Причина: {request.reason}"
            )

        db.close()
    except Exception as e:
        await callback.message.answer(f"Произошла ошибка: {str(e)}")


# Obrabotchiki dlya polzovateley
@router.message(F.text == "📋 Мои заявки")
async def my_requests_handler(message: Message):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)
    if not user:
        await message.answer("Пожалуйста, сначала зарегистрируйтесь используя /start.")
        db.close()
        return

    requests = db.query(Request).filter(Request.user_id == user.id).order_by(Request.created_at.desc()).limit(10).all()

    if not requests:
        await message.answer("Вы еще не отправляли заявок.")
    else:
        response_text = "📋 Ваши последние 10 заявок:\n\n"
        for req in requests:
            response_text += (
                f"**ID:** #{req.id}\n"
                f"**Причина:** {req.reason}\n"
                f"**Статус:** {req.status.title()}\n"
                f"**Дата:** {req.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"---\n"
            )
        await message.answer(response_text)

    db.close()


@router.message(F.text == "ℹ️ Профиль")
async def profile_handler(message: Message):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)
    if not user:
        await message.answer("Пожалуйста, сначала зарегистрируйтесь используя /start.")
        db.close()
        return

    profile_text = (
        "ℹ️ **Ваш профиль**\n\n"
        f"**Полное имя:** {user.full_name}\n"
        f"**Должность:** {user.position}\n"
        f"**Роль:** {user.role.title()}\n"
        f"**Регион:** {user.region}\n"
        f"**Район:** {user.district}\n"
        f"**Учреждение:** {user.institution}\n"
        f"**Зарегистрирован:** {user.created_at.strftime('%Y-%m-%d')}"
    )
    await message.answer(profile_text)

    db.close()


@router.message(F.text == "📊 Моя статистика")
async def technician_stats_handler(message: Message):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)
    if not user or user.role != 'technician':
        await message.answer("❌ У вас нет разрешения на это действие.")
        db.close()
        return

    total_requests_in_institution = db.query(Request).filter(Request.institution == user.institution).count()
    completed = db.query(Request).filter(
        Request.institution == user.institution,
        Request.status == 'completed'
    ).count()
    in_progress = db.query(Request).filter(
        Request.institution == user.institution,
        Request.status == 'in_progress'
    ).count()

    stats_text = (
        "📊 **Статистика по вашему учреждению**\n\n"
        f"**Всего заявок:** {total_requests_in_institution}\n"
        f"**Выполнено:** {completed}\n"
        f"**В процессе:** {in_progress}\n"
    )

    await message.answer(stats_text)
    db.close()


# Obrabotchiki dlya administratora
@router.message(F.text == "📋 Просмотр заявок")
async def admin_view_requests_handler(message: Message):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)
    if not user or user.role != 'admin':
        await message.answer("❌ У вас нет доступа к этому разделу.")
        db.close()
        return

    requests = db.query(Request).filter(
        Request.status.in_(['pending', 'in_progress'])
    ).order_by(Request.created_at.desc()).all()

    if not requests:
        await message.answer("В системе нет активных заявок.")
    else:
        response_text = "📋 **Все активные заявки:**\n\n"
        for req in requests:
            response_text += (
                f"**ID:** #{req.id}\n"
                f"**Учреждение:** {req.institution}\n"
                f"**Причина:** {req.reason}\n"
                f"**Статус:** {req.status.title()}\n"
                f"**Дата:** {req.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"---\n"
            )
        await message.answer(response_text)
    db.close()


@router.message(F.text == "📊 Отчеты")
async def admin_reports_handler(message: Message):
    await generate_report_handler(message)


@router.message(F.text == "🔧 Управление техниками")
async def admin_manage_technicians(message: Message):
    await message.answer(
        "🔧 **Управление техниками**",
        reply_markup=create_admin_manage_technicians_keyboard()
    )


@router.callback_query(F.data == "admin_add_tech")
async def admin_add_technician_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ У вас нет разрешения на это действие.", show_alert=True)
        return

    await callback.message.edit_text(
        "🔧 **Добавление нового техника**\n\n"
        "Пожалуйста, введите Telegram ID нового техника:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel_add_tech")]])
    )
    await state.set_state(AdminAddTechnician.waiting_for_telegram_id)


@router.callback_query(F.data == "cancel_add_tech")
async def cancel_add_technician(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Добавление техника отменено.", reply_markup=None)
    await callback.answer()


@router.message(StateFilter(AdminAddTechnician.waiting_for_telegram_id), F.text)
async def admin_process_technician_id(message: Message, state: FSMContext):
    try:
        telegram_id = int(message.text)
        db = SessionLocal()
        user = get_user_by_telegram_id(db, telegram_id)
        db.close()

        if user:
            await message.answer(
                f"❌ Этот ID ({telegram_id}) уже зарегистрирован.\n"
                "Пожалуйста, введите другой ID или отмените действие."
            )
            return

        await state.update_data(new_tech_telegram_id=telegram_id)
        await message.answer(
            "ID принят. Теперь, пожалуйста, введите полное имя техника:",
            reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
        )
        await state.set_state(AdminAddTechnician.waiting_for_full_name)

    except ValueError:
        await message.answer(
            "❌ Telegram ID должен состоять только из цифр.\n"
            "Пожалуйста, попробуйте снова."
        )


@router.message(StateFilter(AdminAddTechnician.waiting_for_full_name), F.text)
async def admin_process_technician_full_name(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Добавление техника отменено.", reply_markup=create_admin_keyboard())
        return

    await state.update_data(full_name=message.text)
    await message.answer(
        "Имя принято. Теперь, пожалуйста, выберите регион, в котором будет работать техник:",
        reply_markup=create_regions_keyboard()
    )
    await state.set_state(AdminAddTechnician.waiting_for_region)


@router.message(StateFilter(AdminAddTechnician.waiting_for_region), F.text)
async def admin_process_tech_region(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Добавление техника отменено.", reply_markup=create_admin_keyboard())
        return

    await state.update_data(region=message.text)
    await message.answer(
        f"Выбранный регион: {message.text}\n\n"
        "Выберите район:",
        reply_markup=create_districts_keyboard(message.text)
    )
    await state.set_state(AdminAddTechnician.waiting_for_district)


@router.message(StateFilter(AdminAddTechnician.waiting_for_district), F.text)
async def admin_process_tech_district(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Добавление техника отменено.", reply_markup=create_admin_keyboard())
        return

    await state.update_data(district=message.text)
    await message.answer(
        f"Выбранный район: {message.text}\n\n"
        "Выберите учреждение:",
        reply_markup=create_institutions_keyboard(message.text)
    )
    await state.set_state(AdminAddTechnician.waiting_for_institution)


@router.message(StateFilter(AdminAddTechnician.waiting_for_institution), F.text)
async def admin_process_tech_institution(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Добавление техника отменено.", reply_markup=create_admin_keyboard())
        return

    data = await state.get_data()
    telegram_id = data.get('new_tech_telegram_id')
    full_name = data.get('full_name')
    region = data.get('region')
    district = data.get('district')
    institution = message.text

    db = SessionLocal()
    new_technician = create_user(
        db=db,
        telegram_id=telegram_id,
        region=region,
        district=district,
        institution=institution,
        full_name=full_name,
        position="Техник",
        role="technician"
    )
    db.close()
    await state.clear()

    await message.answer(
        "✅ Техник успешно добавлен!\n\n"
        f"**Имя:** {new_technician.full_name}\n"
        f"**Telegram ID:** {new_technician.telegram_id}\n"
        f"**Учреждение:** {new_technician.institution}\n"
        f"**Роль:** Техник\n\n"
        "Новый техник теперь может начать работу, отправив команду /texstart.",
        reply_markup=create_admin_keyboard()
    )

    try:
        await bot.send_message(
            new_technician.telegram_id,
            "🎉 Поздравляем! Вы были добавлены в систему как техник.\n\n"
            "Пожалуйста, отправьте команду `/texstart` чтобы начать работу."
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось отправить сообщение технику. "
            f"Возможно, он заблокировал бота или еще не запустил его.\n\n"
            f"Пожалуйста, свяжитесь с ним и попросите запустить бота."
        )


@router.callback_query(F.data == "admin_delete_tech")
async def admin_delete_technician_start(callback: CallbackQuery):
    db = SessionLocal()
    technicians = db.query(User).filter(User.role == 'technician').all()
    db.close()

    if not technicians:
        await callback.message.edit_text("В системе нет зарегистрированных техников.", reply_markup=None)
        await callback.answer()
        return

    await callback.message.edit_text(
        "❌ **Выберите техника для удаления:**",
        reply_markup=create_delete_technician_keyboard(technicians)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_tech_"))
async def admin_delete_technician(callback: CallbackQuery):
    try:
        technician_id = int(callback.data.split('_')[2])
        db = SessionLocal()
        technician = db.query(User).get(technician_id)

        if technician:
            db.delete(technician)
            db.commit()
            await callback.message.edit_text(
                f"✅ Техник **{technician.full_name}** был успешно удален.",
                reply_markup=None
            )
        else:
            await callback.message.edit_text("❌ Техник не найден.", reply_markup=None)

        db.close()
        await callback.answer()
    except Exception as e:
        await callback.message.answer(f"Произошла ошибка при удалении: {str(e)}")


@router.callback_query(F.data == "cancel_delete")
async def cancel_delete_technician(callback: CallbackQuery):
    await callback.message.edit_text("Удаление техника отменено.", reply_markup=None)
    await callback.answer()


@router.message(F.text == "👥 Управление пользователями")
async def admin_manage_users_handler(message: Message):
    await message.answer("Эта функция пока не реализована. В будущем здесь появится список пользователей для управления их ролями.")


@router.message(F.text == "🏢 Управление данными")
async def admin_manage_data_handler(message: Message):
    await message.answer(
        "🏢 **Управление данными**\n\n"
        "Пожалуйста, выберите действие:",
        reply_markup=create_admin_manage_data_keyboard()
    )


@router.callback_query(F.data == "add_institution")
async def add_institution_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "➕ **Добавление нового учреждения**\n\n"
        "Пожалуйста, выберите регион:",
        reply_markup=create_regions_keyboard()
    )
    await state.set_state(AdminManageData.add_institution_waiting_for_region)


@router.message(StateFilter(AdminManageData.add_institution_waiting_for_region), F.text)
async def process_add_institution_region(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=create_admin_keyboard())
        return

    db = SessionLocal()
    region = db.query(Region).filter(Region.name == message.text).first()
    db.close()

    if not region:
        await message.answer(
            "❌ Выбранный регион не найден. Пожалуйста, выберите из списка.",
            reply_markup=create_regions_keyboard()
        )
        return

    await state.update_data(region_id=region.id, region_name=message.text)
    await message.answer(
        f"Выбранный регион: {message.text}\n\n"
        "Теперь, пожалуйста, выберите район:",
        reply_markup=create_districts_keyboard(message.text)
    )
    await state.set_state(AdminManageData.add_institution_waiting_for_district)


@router.message(StateFilter(AdminManageData.add_institution_waiting_for_district), F.text)
async def process_add_institution_district(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=create_admin_keyboard())
        return

    data = await state.get_data()
    region_id = data.get('region_id')
    district_name = message.text

    db = SessionLocal()
    district = db.query(District).filter(
        District.name == district_name,
        District.region_id == region_id
    ).first()
    db.close()

    if not district:
        await message.answer(
            "❌ Выбранный район не найден в этом регионе. Пожалуйста, выберите из списка.",
            reply_markup=create_districts_keyboard(data.get('region_name'))
        )
        return

    await state.update_data(district_id=district.id)
    await message.answer(
        f"Выбранный район: {district_name}\n\n"
        "Пожалуйста, введите название нового учреждения:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(AdminManageData.add_institution_waiting_for_name)


@router.message(StateFilter(AdminManageData.add_institution_waiting_for_name), F.text)
async def process_add_institution_name(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=create_admin_keyboard())
        return

    data = await state.get_data()
    district_id = data.get('district_id')
    institution_name = message.text

    db = SessionLocal()
    new_institution = Institution(name=institution_name, district_id=district_id)
    db.add(new_institution)
    db.commit()

    await message.answer(
        f"✅ Учреждение **{institution_name}** успешно добавлено!",
        reply_markup=create_admin_keyboard()
    )

    db.close()
    await state.clear()


@router.callback_query(F.data == "delete_institution")
async def delete_institution_start(callback: CallbackQuery):
    db = SessionLocal()
    institutions = db.query(Institution).all()
    db.close()

    if not institutions:
        await callback.message.edit_text("В системе нет зарегистрированных учреждений.", reply_markup=None)
        await callback.answer()
        return

    await callback.message.edit_text(
        "❌ **Выберите учреждение для удаления:**",
        reply_markup=create_delete_institution_keyboard(institutions)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_inst_"))
async def delete_institution(callback: CallbackQuery):
    try:
        institution_id = int(callback.data.split('_')[2])
        db = SessionLocal()
        institution = db.query(Institution).get(institution_id)

        if institution:
            db.delete(institution)
            db.commit()
            await callback.message.edit_text(
                f"✅ Учреждение **{institution.name}** было успешно удалено.",
                reply_markup=None
            )
        else:
            await callback.message.edit_text("❌ Учреждение не найдено.", reply_markup=None)

        db.close()
        await callback.answer()
    except Exception as e:
        await callback.message.answer(f"Произошла ошибка при удалении: {str(e)}")


@router.callback_query(F.data == "back_to_manage_data")
async def back_to_manage_data(callback: CallbackQuery):
    await callback.message.edit_text(
        "🏢 **Управление данными**\n\n"
        "Пожалуйста, выберите действие:",
        reply_markup=create_admin_manage_data_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_admin_menu")
async def back_to_admin_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "С возвращением, Администратор! 👋",
        reply_markup=create_admin_keyboard()
    )
    await callback.answer()


# Glavnaya funktsiya dlya zapuska bota
async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    initialize_sample_data()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot ostanovlen polzovatelem.")