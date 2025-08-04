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
    ADMIN_IDS: List[int] = None # Super Admin ID'lari
    DATABASE_URL: str = "sqlite:///requests.db"
    REPORTS_DIR: str = "reports"

    def __post_init__(self):
        if self.ADMIN_IDS is None:
            self.ADMIN_IDS = [584323689]  # Zamenite na realnyy ID Super administratora

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
    phone_number = Column(String(20), nullable=True)
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
    # Yangi maydonlar (Qo'shilgan)
    resolution_details = Column(Text, nullable=True) # Texnikning izohi: nima qilingani, sababi
    pc_number = Column(String(50), nullable=True) # Kompyuter raqami
    resolved_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=True) # Kim bajargani

    user = relationship("User", back_populates="requests", foreign_keys=[user_id])
    resolver = relationship("User", foreign_keys=[resolved_by_user_id]) # Yangi relationship


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

# (Qo'shilgan) Arizani hal qilish (texniklar uchun)
class RequestResolution(StatesGroup):
    waiting_for_status_confirmation = State()
    waiting_for_pc_number = State()
    waiting_for_resolution_details = State()
    waiting_for_resolution_confirm = State()


class TechnicianRegistration(StatesGroup):
    waiting_for_region = State()
    waiting_for_district = State()
    waiting_for_institution = State()
    waiting_for_full_name = State()
    waiting_for_position = State()
    waiting_for_phone = State()


class AdminAddTechnician(StatesGroup):
    waiting_for_telegram_id = State()
    waiting_for_region = State()
    waiting_for_district = State()
    waiting_for_phone = State()
    waiting_for_institution = State()
    waiting_for_full_name = State()


class AdminManageData(StatesGroup):
    add_institution_waiting_for_region = State()
    add_institution_waiting_for_district = State()
    add_institution_waiting_for_name = State()
    delete_institution_waiting_for_confirmation = State()

# (Qo'shilgan) Super Admin uchun Admin qo'shish
class SuperAdminAddAdmin(StatesGroup):
    waiting_for_admin_telegram_id = State()
    waiting_for_admin_full_name = State()


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


# (O'zgartirilgan) Admin klaviaturasi
def create_admin_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📋 Arizalarni ko'rish"), KeyboardButton(text="📊 Hisobotlar")],
        [KeyboardButton(text="🔧 Texniklarni boshqarish"), KeyboardButton(text="🔧 Texniklar haqida ma'lumot")],
        [KeyboardButton(text="🏢 Ma'lumotlarni boshqarish"), KeyboardButton(text="👥 Foydalanuvchi & Texniklar soni")],
        [KeyboardButton(text="👑 Admin qo'shish")]
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


# (O'zgartirilgan) Institution o'chirish klaviaturasi
def create_delete_institution_keyboard(institutions: List[tuple]) -> InlineKeyboardMarkup:
    buttons = []
    # institutions - bu (Institution object, district_name) tuplelar ro'yxati
    for institution, district_name in institutions:
        button_text = f"{district_name}: {institution.name}"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"delete_inst_{institution.id}")])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_manage_data")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Generator otchetov v formate PDF
class PDFReportGenerator:
    def __init__(self, db: Session):
        self.db = db
        self.styles = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(name='TableText', fontSize=7, leading=9))
        self.styles.add(ParagraphStyle(name='TableBold', fontSize=8, leading=10, fontName='Helvetica-Bold'))

    def generate_weekly_report(self, start_date: datetime, end_date: datetime) -> str:
        """Generatsiya yezhenedelnogo PDF-otcheta"""
        filename = f"weekly_report_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.pdf"
        filepath = os.path.join(config.REPORTS_DIR, filename)

        doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=30, rightMargin=30, topMargin=30, bottomMargin=30)
        story = []

        title_style = ParagraphStyle(
            'CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=18,
            spaceAfter=30,
            alignment=1
        )
        story.append(
            Paragraph(f"Zayafkalar haqida otchod ({start_date.strftime('%Y-%m-%d')} po {end_date.strftime('%Y-%m-%d')})",
                      title_style))
        story.append(Spacer(1, 20))

        requests = self.db.query(Request).filter(
            Request.created_at >= start_date,
            Request.created_at <= end_date + timedelta(days=1, seconds=-1)
        ).all()

        total_requests = len(requests)
        completed_requests = sum(1 for r in requests if r.status == 'completed')
        in_progress_requests = sum(1 for r in requests if r.status == 'in_progress')
        pending_requests = sum(1 for r in requests if r.status == 'pending')
        not_completed_requests = sum(1 for r in requests if r.status == 'not_completed')

        summary_data = [
            ['Status', 'Soni', 'Foyiz'],
            ['Zayafkalr soni', str(total_requests), '100%'],
            ['Bajarilgan ', str(completed_requests),
             f'{completed_requests / total_requests * 100:.1f}%' if total_requests > 0 else '0%'],
            ['Protsesda', str(in_progress_requests),
             f'{in_progress_requests / total_requests * 100:.1f}%' if total_requests > 0 else '0%'],
            ['Kutilyadi', str(pending_requests),
             f'{pending_requests / total_requests * 100:.1f}%' if total_requests > 0 else '0%'],
            ['Bajarilmagan zayafkalar', str(not_completed_requests),
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
        story.append(Spacer(1, 30))

        if requests:
            story.append(Paragraph("Zayafkalar royxati", self.styles['Heading2']))
            story.append(Spacer(1, 12))

            table_data = [
                ['ID', 'Foydalanuvchi', 'Region', 'Rayon', 'Tashkilot', 'Sababi', 'PC', 'Status', 'Sozdano', 'Ish bajarildi'] # Yangi ustun 'PC' va 'Ish bajarildi'
            ]

            for req in requests:
                reason_text = req.reason
                if len(reason_text) > 50: # Cheklovni 50 belgiga tushirdik
                    reason_text = reason_text[:50] + '...'

                resolution_text = req.resolution_details or "N/A" # Yangi
                if len(resolution_text) > 50:
                    resolution_text = resolution_text[:50] + '...'

                row = [
                    Paragraph(str(req.id), self.styles['TableText']),
                    Paragraph(req.user.full_name, self.styles['TableText']),
                    Paragraph(req.region, self.styles['TableText']),
                    Paragraph(req.district, self.styles['TableText']),
                    Paragraph(req.institution, self.styles['TableText']),
                    Paragraph(reason_text, self.styles['TableText']),
                    Paragraph(req.pc_number or 'N/A', self.styles['TableText']), # PC raqami
                    Paragraph(req.status.title(), self.styles['TableText']),
                    Paragraph(req.created_at.strftime('%Y-%m-%d %H:%M'), self.styles['TableText']),
                    Paragraph(resolution_text, self.styles['TableText']), # Ish bajarildi izohi
                ]
                table_data.append(row)

            # Ustunlarning aniq kengliklari (umumiy 540 punkt)
            col_widths = [
                20,  # ID
                60,  # Polzovatel
                60,  # Region
                60,  # Rayon
                80,  # Uchrezhdeniye
                70,  # Prichina (qisqartirilgan)
                30,  # PC
                40,  # Status
                70,  # Sozdano
                50 # Ish bajarildi
            ]

            detailed_table = Table(table_data, colWidths=col_widths)
            detailed_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 8), # Kichikroq shrift
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))

            story.append(detailed_table)

        doc.build(story)
        return filepath




# Initsializatsiya bota
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()


def initialize_sample_data():
    db = SessionLocal()

    # Проверка существования данных, чтобы не добавлять их повторно
    if db.query(Region).first():
        db.close()
        return

    print("Начинаем заполнение базы данных всеми регионами и районами...")

    # Данные для всех регионов и их районов
    data = {
        "Республика Каракалпакстан": ["Амударьинский район", "Берунийский район", "Бозатауский район",
                                      "Кегейлийский район", "Кунградский район", "Канлыкульский район",
                                      "Муйнакский район", "Нукусский район", "Тахиаташский район", "Турткульский район",
                                      "Ходжейлийский район", "Чимбайский район", "Шуманайский район",
                                      "Элликкалинский район"],
        "Андижанская область": ["Андижанский район", "Асакинский район", "Алтынкульский район", "Балыкчинский район",
                                "Бустанский район", "Булакбашинский район", "Джалакудукский район",
                                "Избасканский район", "Кургантепинский район", "Мархаматский район",
                                "Пахтаабадский район", "Улугнорский район", "Ходжаабадский район",
                                "Шахриханский район"],
        "Бухарская область": ["Алатский район", "Бухарский район", "Вабкентский район", "Гиждуванский район",
                              "Джандаринский район", "Каганский район", "Каракульский район", "Караулбазарский район",
                              "Пешкунский район", "Ромитанский район", "Шафирканский район"],
        "Джизакская область": ["Арнасайский район", "Бахмальский район", "Галляаральский район", "Дустликский район",
                               "Джизакский район", "Зааминский район", "Зарбдорский район", "Зафарабадский район",
                               "Мирзачульский район", "Пахтакорский район", "Фаришский район", "Янгиабадский район"],
        "Кашкадарьинская область": ["Камашинский район", "Каршинский район", "Касбинский район", "Китабский район",
                                    "Мубарекский район", "Нишанский район", "Гузарский район", "Чиракчинский район",
                                    "Шахрисабзский район", "Яккабагский район", "Миришкорский район",
                                    "Кумкурганский район"],
        "Навоийская область": ["Карманинский район", "Кызылтепинский район", "Навбахорский район", "Канимехский район",
                               "Нуратинский район", "Тамадинский район", "Учкудукский район", "Хатырчинский район"],
        "Наманганская область": ["Наманганский район", "Касансайский район", "Мингбулакский район", "Нарынский район",
                                 "Папский район", "Туракурганский район", "Учкурганский район", "Чартакский район",
                                 "Чустский район", "Янгикурганский район", "Чортокский район"],
        "Самаркандская область": ["Акдарьинский район", "Булунгурский район", "Джамбайский район", "Иштиханский район",
                                  "Каттакурганский район", "Кушрабатский район", "Нарпайский район",
                                  "Нурабадский район", "Пайарыкский район", "Пастдаргамский район",
                                  "Пахтачинский район", "Самаркандский район", "Тайлакский район", "Ургутский район"],
        "Сурхандарьинская область": ["Ангорский район", "Байсунский район", "Бандиханский район", "Денауский район",
                                     "Джаркурганский район", "Кумкурганский район", "Кызырыкский район",
                                     "Музрабадский район", "Сариасийский район", "Термезский район", "Узунский район",
                                     "Шерабадский район", "Шурчинский район"],
        "Сырдарьинская область": ["Акалтынский район", "Баяутский район", "Гулистанский район", "Мирзаабадский район",
                                  "Сардобинский район", "Сайхунабадский район", "Сырдарьинский район",
                                  "Хавастский район"],
        "Ташкентская область": ["Ахангаранский район", "Бекабадский район", "Букинский район", "Бостанлыкский район",
                                "Куйичирчикский район", "Зангиатинский район", "Юкоричирчикский район",
                                "Кибрайский район", "Паркентский район", "Пскентский район", "Ташкентский район",
                                "Чирчикский район", "Янгиюльский район"],
        "Ферганская область": ["Алтыарыкский район", "Багдадский район", "Бешарыкский район", "Бувайдинский район",
                               "Кувинский район", "Риштанский район", "Сохский район", "Ташлакский район",
                               "Узбекистанский район", "Учкуприкский район", "Ферганский район", "Фуркатский район"],
        "Хорезмская область": ["Багатский район", "Гурленский район", "Кошкупирский район", "Ургенчский район",
                               "Хазараспский район", "Хивинский район", "Хонкинский район", "Шаватский район",
                               "Янгиарыкский район", "Янгибазарский район"],
        "Город Ташкент": ["Бектемирский район", "Мирабадский район", "Мирзо-Улугбекский район", "Сергелийский район",
                          "Учтепинский район", "Чиланзарский район", "Шайхантахурский район", "Юнусабадский район",
                          "Яккасарайский район"]
    }

    # Создаем регионы и маппинг для их ID
    regions_map = {}
    for region_name in data.keys():
        region = Region(name=region_name)
        db.add(region)
        regions_map[region_name] = region
    db.commit()

    # Создаем районы, используя ID регионов
    for region_name, districts_list in data.items():
        region_id = regions_map[region_name].id
        for district_name in districts_list:
            district = District(name=district_name, region_id=region_id)
            db.add(district)
    db.commit()

    # Добавляем примеры учреждений
    institutions = [
        Institution(name="1-я Семейная поликлиника",
                    district_id=db.query(District).filter_by(name="Мирабадский район").first().id),
        Institution(name="2-я Семейная поликлиника",
                    district_id=db.query(District).filter_by(name="Юнусабадский район").first().id),
        Institution(name="Городская больница №1",
                    district_id=db.query(District).filter_by(name="Чиланзарский район").first().id),
        Institution(name="Детский сад №5",
                    district_id=db.query(District).filter_by(name="Юкоричирчикский район").first().id),
    ]
    db.add_all(institutions)
    db.commit()

    db.close()
    print("База данных успешно заполнена. Вы готовы к работе!")

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

    await state.update_data(position=message.text)
    await message.answer(
        "📱 Iltimos, telefon raqamingizni kiriting (namuna: +998901234567):",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(TechnicianRegistration.waiting_for_phone)

@router.message(StateFilter(TechnicianRegistration.waiting_for_phone), F.text)
async def process_technician_phone(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Регистрация отменена.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/texstart")]], resize_keyboard=True))
        return

    # Telefon raqamini tekshirish
    phone_number = message.text.strip()
    if not phone_number.startswith('+') or not phone_number[1:].isdigit():
        await message.answer("❌ Noto'g'ri telefon raqam formati. Iltimos, +998901234567 formatida kiriting.")
        return

    data = await state.get_data()
    db = SessionLocal()
    user = create_user(
        db, message.from_user.id,
        data['region'], data['district'],
        data['institution'],
        data['full_name'],
        data['position'],
        'technician'
    )
    user.phone_number = phone_number  # Telefon raqamini qo'shamiz
    db.commit()
    db.close()

    await state.clear()
    await message.answer(
        "✅ Texnik ro'yxatdan o'tkazildi!\n\n"
        f"📱 Telefon: {phone_number}\n\n"
        "Endi siz texnik sifatida ishlay olasiz!",
        reply_markup=create_technician_keyboard()
    )


@router.message(F.text == "🔧 Texniklar haqida ma'lumot")
async def admin_technicians_info(message: Message):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)
    if not user or user.role != 'admin':
        await message.answer("❌ Ruxsat yo'q")
        db.close()
        return

    technicians = db.query(User).filter(User.role == 'technician').order_by(User.region, User.district).all()

    if not technicians:
        await message.answer("❌ Texniklar topilmadi")
    else:
        response = "📊 Texniklar haqida ma'lumot:\n\n"
        for tech in technicians:
            response += (
                f"👤 <b>{tech.full_name}</b>\n"
                f"📱 Tel: {tech.phone_number or 'N/A'}\n"
                f"🏢 {tech.institution} ({tech.district}, {tech.region})\n"
                f"🆔 ID: {tech.telegram_id}\n"
                f"📅 Ro'yxatdan o'tgan: {tech.created_at.strftime('%Y-%m-%d')}\n"
                f"---\n"
            )
        await message.answer(response, parse_mode='HTML')

    db.close()



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
            "Ваша заявка была отправлена администраторам и техникам.\n\n"
            "\nПожалуйста, отправьте команду✅ /start ✅повторно.",

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
                    f"🔔 **Вам поступила новая заявка:**\n\n"
                         f"🆔 **ID:** #{request.id}\n"
                         f"🏢 **Учреждение:** {request.institution}\n"
                         f"📝 **Причина:** {request.reason}\n"
                         f"📍 **Этаж/Комната:** {request.floor_room}\n"
                         f"➡️ **Статус:** В ожидании\n\n"
                         "Для возвращения в главное меню нажмите /start"
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
                f"🆔 **ID:** #{req.id}\n"
                f"📝 **Причина:** {req.reason}\n"
                f"📍 **Этаж/Комната:** {req.floor_room}\n"
                f"➡️ **Статус:** {req.status.title()}\n"
                f"👤 **Отправил:** {req.submitted_by}\n"
                f"🗓️ **Дата:** {req.created_at.strftime('%Y-%m-%d %H:%M')}\n"
            )
            await message.answer(
                response_text,
                reply_markup=create_request_status_keyboard(req.id)
            )

    db.close()

# (O'zgartirilgan) Texniklar uchun statusni yangilash handleri (izoh qoldirish uchun)
@router.callback_query(F.data.startswith("status_"))
async def initiate_request_status_update(callback: CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split('_')
        new_status = parts[1]
        request_id = int(parts[2])

        db = SessionLocal()
        request = db.query(Request).get(request_id)
        technician = get_user_by_telegram_id(db, callback.from_user.id)

        if not request or request.institution != technician.institution:
            await callback.answer("❌ Sizda bu arizaning statusini o'zgartirishga ruxsat yo'q.", show_alert=True)
            db.close()
            return

        # Agar status "completed" yoki "not_completed" bo'lsa, izoh so'raymiz
        if new_status in ['completed', 'not_completed']:
            await state.update_data(
                request_id=request_id,
                new_status=new_status,
                technician_id=technician.id
            )
            await callback.message.edit_text(
                f"➡️ **Arizaning yangi holati:** **'{new_status.title()}'**\n\n"
                     f"💻 **Iltimos, PC raqamini kiriting:**\n"
                     f"(Agar PC bo'lmasa, **'Yo'q'** deb yozing)",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Bekor qilish", callback_data="cancel_resolution")]
                ])
            )
            await state.set_state(RequestResolution.waiting_for_pc_number)
        else:
            # Agar status "in_progress" bo'lsa, darhol yangilaymiz
            request.status = new_status
            request.resolved_by_user_id = technician.id # Kim bajarganini yozamiz
            db.commit()
            db.refresh(request)

            await callback.message.edit_text(
                f"✅ **Статус заявки #{request_id} обновлен на '{new_status.title()}'**"                "Пожалуйста, нажмите кнопку ниже или отправьте команду✅  /start ✅ повторно.",
                reply_markup=None
            )

            user_who_submitted = db.query(User).get(request.user_id)
            if user_who_submitted:
                await bot.send_message(
                    user_who_submitted.telegram_id,
                    f"🔔 Arizangiz #{request.id} yangilandi:\n\n"
                    f"Status: **{new_status.title()}**.\n\n"
                    f"Sabab: {request.reason}"
                )

        db.close()
        await callback.answer() # Callback queryga javob qaytarish
    except Exception as e:
        logging.error(f"Arizaning statusini yangilashda xato: {e}")
        await callback.message.answer(f"Xatolik yuz berdi: {str(e)}")


# (Qo'shilgan) Arizani hal qilish (texniklar uchun)
@router.callback_query(F.data == "cancel_resolution", StateFilter(RequestResolution))
async def cancel_resolution(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Arizani hal qilish bekor qilindi.", reply_markup=None)
    await callback.answer()

@router.message(StateFilter(RequestResolution.waiting_for_pc_number), F.text)
async def process_pc_number(message: Message, state: FSMContext):
    if message.text.lower() == "bekor qilish":
        await state.clear()
        await message.answer("Arizani hal qilish bekor qilindi.", reply_markup=create_technician_keyboard())
        return

    await state.update_data(pc_number=message.text)
    await message.answer(
        f"📝 **Напишите, что было сделано, и укажите причину проблемы:**\n"
             f"*(Например: '💻 Компьютер был очищен, так как работал медленно.' или '🖨️ Заменили картридж в принтере, так как закончились чернила.')*",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Bekor qilish")]], resize_keyboard=True)
    )
    await state.set_state(RequestResolution.waiting_for_resolution_details)


@router.message(StateFilter(RequestResolution.waiting_for_resolution_details), F.text)
async def process_resolution_details(message: Message, state: FSMContext):
    if message.text.lower() == "bekor qilish":
        await state.clear()
        await message.answer("Arizani hal qilish bekor qilindi.", reply_markup=create_technician_keyboard())
        return

    await state.update_data(resolution_details=message.text)
    data = await state.get_data()

    confirmation_text = (
        f"✅ **Arizani yakunlash tasdigʻi:**\n\n"
        f"🆔 **Ariza ID:** #{data['request_id']}\n"
        f"➡️ **Yangi holati:** {data['new_status'].title()}\n"
        f"💻 **Kompyuter raqami:** {data['pc_number']}\n"
        f"📝 **Izoh:** {data['resolution_details']}\n\n"
        "**Maʼlumotlar toʻgʻrimi?**"
    )
    await message.answer(
        confirmation_text,
        reply_markup=create_confirmation_keyboard() # confirm_yes/confirm_no
    )
    await state.set_state(RequestResolution.waiting_for_resolution_confirm)


@router.callback_query(StateFilter(RequestResolution.waiting_for_resolution_confirm))
async def confirm_resolution_details(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    request_id = data['request_id']
    new_status = data['new_status']
    pc_number = data['pc_number']
    resolution_details = data['resolution_details']
    technician_id = data['technician_id']

    db = SessionLocal()
    request = db.query(Request).get(request_id)
    technician = db.query(User).get(technician_id) # Texnik ma'lumotlarini olish

    if not request:
        await callback.message.edit_text("Arizada xatolik topildi, u mavjud emas.", reply_markup=None)
        await state.clear()
        db.close()
        return

    if callback.data == "confirm_yes":
        request.status = new_status
        request.pc_number = pc_number
        request.resolution_details = resolution_details
        request.resolved_by_user_id = technician_id # Kim bajarganini yozamiz
        db.commit()
        db.refresh(request)

        await callback.message.edit_text(
            f"✅ **Статус заявки #{request_id} обновлен на '{new_status.title()}'**\n\n"
                 f"💻 **Номер ПК:** {pc_number}\n"
                 f"📝 **Описание:** {resolution_details}\n\n"
                 f"➡️ Чтобы вернуться в главное меню, нажмите /start.",
            reply_markup=None
        )

        user_who_submitted = db.query(User).get(request.user_id)
        if user_who_submitted:
            await bot.send_message(
                user_who_submitted.telegram_id,
                f"🔔 Arizangiz #{request.id} yangilandi:\n\n"
                f"Status: **{new_status.title()}**.\n"
                f"PC raqami: {pc_number or 'Mavjud emas'}\n"
                f"Texnik izohi: {resolution_details}\n\n"
                f"Sabab: {request.reason}"
            )
        # Adminlarga ham xabar yuborish
        admins = db.query(User).filter(User.role == 'admin').all()
        for admin in admins:
            try:
                await bot.send_message(
                    admin.telegram_id,
                    f"✅ Ariza bajarildi: #{request.id}\n\n"
                    f"**Kim bajardi:** {technician.full_name if technician else 'Nomalum'}\n"
                    f"**Status:** {new_status.title()}\n"
                    f"**Uchrezhdeniye:** {request.institution}\n"
                    f"**PC raqami:** {pc_number or 'Mavjud emas'}\n"
                    f"**Sabab:** {request.reason}\n"
                    f"**Izoh:** {resolution_details}"
                )
            except Exception as e:
                logging.error(f"Administratorga ({admin.telegram_id}) xabar yuborishda xato: {e}")

    else:
        await callback.message.edit_text("❌ Arizani hal qilish bekor qilindi.", reply_markup=None)

    db.close()
    await state.clear()


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
                f"🆔 **ID:** #{req.id}\n"
                f"📝 **Причина:** {req.reason}\n"
                f"➡️ **Статус:** {req.status.title()}\n"
                f"🗓️ **Дата:** {req.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"---"
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
        f"ℹ️ **Ваш профиль**\n\n"
        f"👤 **Полное имя:** {user.full_name}\n"
        f"💼 **Должность:** {user.position}\n"
        f"🔑 **Роль:** {user.role.title()}\n"
        f"🌍 **Регион:** {user.region}\n"
        f"🏘️ **Район:** {user.district}\n"
        f"🏢 **Учреждение:** {user.institution}\n"
        f"🗓️ **Зарегистрирован:** {user.created_at.strftime('%Y-%m-%d')}"
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
        f"➡️ **Всего заявок:** {total_requests_in_institution}\n"
        f"✅ **Выполнено:** {completed}\n"
        f"🔄 **В процессе:** {in_progress}\n"
    )

    await message.answer(stats_text)
    db.close()


# Obrabotchiki dlya administratora
@router.message(F.text == "📋 Arizalarni ko'rish") # Matn o'zgartirildi
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
                f"🆔 **ID:** -- {req.id}\n"
                f"🏢 **Учреждение:** {req.institution}\n"
                f"📝 **Причина:** {req.reason}\n"
                f"➡️ **Статус:** {req.status.title()}\n"
                f"🗓️ **Дата:** {req.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"---\n\n"
            )
        await message.answer(response_text)
    db.close()


@router.message(F.text == "📊 Hisobotlar") # Matn o'zgartirildi
async def admin_reports_handler(message: Message):
    await generate_report_handler(message)


@router.message(F.text == "🔧 Texniklarni boshqarish") # Matn o'zgartirildi
async def admin_manage_technicians(message: Message):
    await message.answer(
        "🔧 **Texniklarni boshqarish**",
        reply_markup=create_admin_manage_technicians_keyboard()
    )


@router.callback_query(F.data == "admin_add_tech")
async def admin_add_technician_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ У вас нет разрешения на это действие.", show_alert=True)
        return

    await callback.message.edit_text(
        "🔧 **Yangi texnik qo'shish**\n\n" # Matn o'zgartirildi
        "Iltimos, yangi texnikning Telegram ID'sini kiriting:", # Matn o'zgartirildi
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel_add_tech")]])
    )
    await state.set_state(AdminAddTechnician.waiting_for_telegram_id)


@router.callback_query(F.data == "cancel_add_tech")
async def cancel_add_technician(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Texnik qo'shish bekor qilindi.", reply_markup=None) # Matn o'zgartirildi
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
                f"❌ Bu ID ({telegram_id}) allaqachon ro'yxatdan o'tgan.\n" # Matn o'zgartirildi
                "Iltimos, boshqa ID kiriting yoki bekor qiling." # Matn o'zgartirildi
            )
            return

        await state.update_data(new_tech_telegram_id=telegram_id)
        await message.answer(
            "ID qabul qilindi. Endi, iltimos, texnikning to'liq ismini kiriting:", # Matn o'zgartirildi
            reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
        )
        await state.set_state(AdminAddTechnician.waiting_for_full_name)

    except ValueError:
        await message.answer(
            "❌ Telegram ID faqat raqamlardan iborat bo'lishi kerak.\n" # Matn o'zgartirildi
            "Iltimos, qaytadan urinib ko'ring." # Matn o'zgartirildi
        )


@router.message(StateFilter(AdminAddTechnician.waiting_for_full_name), F.text)
async def admin_process_technician_full_name(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Texnik qo'shish bekor qilindi.", reply_markup=create_admin_keyboard())
        return

    await state.update_data(full_name=message.text)
    await message.answer(
        "📱 Iltimos, texnikning telefon raqamini kiriting (namuna: +998901234567):",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(AdminAddTechnician.waiting_for_phone)


@router.message(StateFilter(AdminAddTechnician.waiting_for_phone), F.text)
async def admin_process_technician_phone(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Texnik qo'shish bekor qilindi.", reply_markup=create_admin_keyboard())
        return

    phone_number = message.text.strip()
    if not phone_number.startswith('+') or not phone_number[1:].isdigit():
        await message.answer(
            "❌ Noto'g'ri telefon raqam formati. Iltimos, +998901234567 formatida kiriting."
        )
        return

    await state.update_data(phone_number=phone_number)
    await message.answer(
        "Telefon raqami qabul qilindi. Endi, iltimos, texnik ishlaydigan mintaqani tanlang:",
        reply_markup=create_regions_keyboard()
    )
    await state.set_state(AdminAddTechnician.waiting_for_region)

@router.message(StateFilter(AdminAddTechnician.waiting_for_region), F.text)
async def admin_process_tech_region(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Texnik qo'shish bekor qilindi.", reply_markup=create_admin_keyboard())
        return

    await state.update_data(region=message.text)
    await message.answer(
        f"Tanlangan mintaqa: {message.text}\n\n"
        "Tumanni tanlang:",
        reply_markup=create_districts_keyboard(message.text)
    )
    await state.set_state(AdminAddTechnician.waiting_for_district)


@router.message(StateFilter(AdminAddTechnician.waiting_for_district), F.text)
async def admin_process_tech_district(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Texnik qo'shish bekor qilindi.", reply_markup=create_admin_keyboard())
        return

    await state.update_data(district=message.text)
    await message.answer(
        f"Tanlangan tuman: {message.text}\n\n"
        "Muassasani tanlang:",
        reply_markup=create_institutions_keyboard(message.text)
    )
    await state.set_state(AdminAddTechnician.waiting_for_institution)

@router.message(StateFilter(AdminAddTechnician.waiting_for_institution), F.text)
async def admin_process_tech_institution(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Texnik qo'shish bekor qilindi.", reply_markup=create_admin_keyboard())
        return

    data = await state.get_data()
    telegram_id = data.get('new_tech_telegram_id')
    full_name = data.get('full_name')
    phone_number = data.get('phone_number')
    region = data.get('region')
    district = data.get('district')
    institution = message.text

    # Muassasa mavjudligini tekshirish
    db = SessionLocal()
    try:
        institution_exists = db.query(Institution).join(District).filter(
            Institution.name == institution,
            District.name == district
        ).first()

        if not institution_exists:
            await message.answer(
                f"❌ Muassasa '{institution}' {district} tumanida topilmadi. Iltimos, ro'yxatdan tanlang.",
                reply_markup=create_institutions_keyboard(district)
            )
            db.close()
            return

        # Yangi texnikni yaratish
        new_technician = create_user(
            db=db,
            telegram_id=telegram_id,
            region=region,
            district=district,
            institution=institution,
            full_name=full_name,
            position="Texnik",
            role="technician"
        )
        new_technician.phone_number = phone_number
        db.commit()

        # Muvaffaqiyatli xabar
        await message.answer(
            "<b>🎉 Texnik muvaffaqiyatli qo'shildi!</b>\n\n"
            "📋 <b>Ma'lumotlar:</b>\n"
            f"👤 <b>Ism:</b> {new_technician.full_name}\n"
            f"📱 <b>Telefon:</b> {phone_number}\n"
            f"🆔 <b>Telegram ID:</b> {new_technician.telegram_id}\n"
            f"🏢 <b>Muassasa:</b> {new_technician.institution}\n"
            f"🔧 <b>Rol:</b> Texnik\n\n"
            "<i>Yangi texnik endi <code>/texstart</code> buyrug'ini yuborib ish boshlashi mumkin.</i>",
            parse_mode="HTML",
            reply_markup=create_admin_keyboard()
        )

        # Yangi texnikka xabar yuborish
        try:
            await bot.send_message(
                new_technician.telegram_id,
                "🎉 Tabriklaymiz! Siz tizimga texnik sifatida qo'shildingiz.\n\n"
                "Iltimos, ish boshlash uchun `/texstart` buyrug'ini yuboring."
            )
        except Exception as e:
            await message.answer(
                f"❌ Texnikka xabar yuborishda xatolik yuz berdi: {str(e)}.\n"
                f"Ehtimol, u botni bloklagan yoki hali ishga tushirmagan.\n\n"
                f"Iltimos, u bilan bog'lanib, botni ishga tushirishini so'rang."
            )

    except Exception as e:
        logging.error(f"Texnik qo'shishda xatolik: {str(e)}")
        await message.answer(
            f"❌ Texnik qo'shishda xatolik yuz berdi: {str(e)}.\nIltimos, qaytadan urinib ko'ring.",
            reply_markup=create_admin_keyboard()
        )
    finally:
        db.close()
        await state.clear()

@router.callback_query(F.data == "admin_delete_tech")
async def admin_delete_technician_start(callback: CallbackQuery):
    db = SessionLocal()
    technicians = db.query(User).filter(User.role == 'technician').all()
    db.close()

    if not technicians:
        await callback.message.edit_text("Tizimda ro'yxatdan o'tgan texniklar yo'q.", reply_markup=None) # Matn o'zgartirildi
        await callback.answer()
        return

    await callback.message.edit_text(
        "❌ **O'chirish uchun texnikni tanlang:**", # Matn o'zgartirildi
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
                f"✅ Texnik **{technician.full_name}** muvaffaqiyatli o'chirildi.", # Matn o'zgartirildi
                reply_markup=None
            )
        else:
            await callback.message.edit_text("❌ Texnik topilmadi.", reply_markup=None) # Matn o'zgartirildi

        db.close()
        await callback.answer()
    except Exception as e:
        await callback.message.answer(f"O'chirishda xatolik yuz berdi: {str(e)}") # Matn o'zgartirildi


@router.callback_query(F.data == "cancel_delete")
async def cancel_delete_technician(callback: CallbackQuery):
    await callback.message.edit_text("Texnikni o'chirish bekor qilindi.", reply_markup=None) # Matn o'zgartirildi
    await callback.answer()


# Administrator handlerlari qismida
@router.message(F.text == "👥 Foydalanuvchi & Texniklar soni")
async def admin_users_and_techs_count_handler(message: Message):
    db = SessionLocal()
    user = get_user_by_telegram_id(db, message.from_user.id)
    if not user or user.role != 'admin':
        await message.answer("❌ Sizda bu bo'limga kirishga ruxsat yo'q.")
        db.close()
        return

    try: # Qo'shimcha try-except bloki qo'shish
        total_users = db.query(User).filter(User.role == 'user').count()
        total_technicians = db.query(User).filter(User.role == 'technician').count()
        total_active_requests = db.query(Request).filter(Request.status.in_(['pending', 'in_progress'])).count()
        total_completed_requests = db.query(Request).filter(Request.status == 'completed').count()
        total_requests_all = db.query(Request).count()

        stats_text = (
            "📊 **Tizim statistikasi:**\n\n"
            f"👥 Umumiy foydalanuvchilar (ariza beruvchilar) soni: **{total_users}**\n"
            f"🔧 Umumiy texniklar soni: **{total_technicians}**\n\n"
            f"📋 Umumiy arizalar soni: **{total_requests_all}**\n"
            f"⏳ Faol (kutayotgan/jarayondagi) arizalar: **{total_active_requests}**\n"
            f"✅ Bajarilgan arizalar: **{total_completed_requests}**"
        )

        await message.answer(stats_text)
    except Exception as e:
        # Agar xato bo'lsa, uni logga yozamiz va foydalanuvchiga xabar beramiz
        logging.error(f"Statistika olishda xato yuz berdi: {e}")
        await message.answer("❌ Statistik ma'lumotlarni olishda xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")
    finally:
        db.close() # Har doim db ulanishini yopishni unutmang

@router.message(F.text == "🏢 Ma'lumotlarni boshqarish") # Matn o'zgartirildi
async def admin_manage_data_handler(message: Message):
    await message.answer(
        "🏢 **Ma'lumotlarni boshqarish**\n\n" # Matn o'zgartirildi
        "Iltimos, harakatni tanlang:", # Matn o'zgartirildi
        reply_markup=create_admin_manage_data_keyboard()
    )


# Yangi muassasa qo'shish holatlari
class AdminAddInstitution(StatesGroup):
    waiting_for_region = State()
    waiting_for_district = State()
    waiting_for_name = State()


@router.callback_query(F.data == "add_institution")
async def add_institution_start(callback: CallbackQuery, state: FSMContext):
    # Вместо редактирования, отправляем новое сообщение
    await callback.message.answer(
        "➕ **Yangi muassasa qo'shish**\n\n" # Matn o'zgartirildi
        "Iltimos, mintaqani tanlang:", # Matn o'zgartirildi
        reply_markup=create_regions_keyboard()
    )

    # Также не забудьте удалить старое сообщение с инлайн-клавиатурой,
    # чтобы оно не мешало
    await callback.message.delete()

    await state.set_state(AdminAddInstitution.waiting_for_region)

@router.message(StateFilter(AdminAddInstitution.waiting_for_region), F.text)
async def process_add_institution_region(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Harakat bekor qilindi.", reply_markup=create_admin_keyboard()) # Matn o'zgartirildi
        return

    db = SessionLocal()
    region = db.query(Region).filter(Region.name == message.text).first()
    db.close()

    if not region:
        await message.answer(
            "❌ Tanlangan mintaqa topilmadi. Iltimos, ro'yxatdan tanlang.", # Matn o'zgartirildi
            reply_markup=create_regions_keyboard()
        )
        return

    await state.update_data(region_id=region.id, region_name=message.text)
    await message.answer(
        f"Tanlangan mintaqa: {message.text}\n\n" # Matn o'zgartirildi
        "Endi, iltimos, tumanni tanlang:", # Matn o'zgartirildi
        reply_markup=create_districts_keyboard(message.text)
    )
    await state.set_state(AdminAddInstitution.waiting_for_district)

@router.message(StateFilter(AdminAddInstitution.waiting_for_district), F.text)
async def process_add_institution_district(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Harakat bekor qilindi.", reply_markup=create_admin_keyboard()) # Matn o'zgartirildi
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
            "❌ Tanlangan tuman bu mintaqada topilmadi. Iltimos, ro'yxatdan tanlang.", # Matn o'zgartirildi
            reply_markup=create_districts_keyboard(data.get('region_name'))
        )
        return

    await state.update_data(district_id=district.id)
    await message.answer(
        f"Tanlangan tuman: {district_name}\n\n" # Matn o'zgartirildi
        "Iltimos, yangi muassasaning nomini kiriting:", # Matn o'zgartirildi
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(AdminAddInstitution.waiting_for_name)

@router.message(StateFilter(AdminAddInstitution.waiting_for_name), F.text)
async def process_add_institution_name(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        await message.answer("Harakat bekor qilindi.", reply_markup=create_admin_keyboard()) # Matn o'zgartirildi
        return

    data = await state.get_data()
    district_id = data.get('district_id')
    institution_name = message.text

    try:
        db = SessionLocal()
        new_institution = Institution(name=institution_name, district_id=district_id)
        db.add(new_institution)
        db.commit()

        await message.answer(
            f"✅ Muassasa **{institution_name}** muvaffaqiyatli qo'shildi!", # Matn o'zgartirildi
            reply_markup=create_admin_keyboard()
        )

        db.close()
        await state.clear()

    except Exception as e:
        # Если произошла ошибка, выведет её в консоль
        logging.error(f"Muassasa qo'shishda xatolik: {e}") # Matn o'zgartirildi
        await message.answer(
            f"❌ Muassasa qo'shishda xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.", # Matn o'zgartirildi
            reply_markup=create_admin_keyboard()
        )
        await state.clear()

@router.callback_query(F.data == "delete_institution")
async def delete_institution_start(callback: CallbackQuery):
    db = SessionLocal()

    # ИСПРАВЛЕНО: получаем учреждения вместе с районами, к которым они относятся
    institutions_with_districts = db.query(Institution, District.name).join(District).all()
    db.close()

    if not institutions_with_districts:
        await callback.message.edit_text("Tizimda ro'yxatdan o'tgan muassasalar yo'q.", reply_markup=None) # Matn o'zgartirildi
        await callback.answer()
        return

    await callback.message.edit_text(
        "❌ **O'chirish uchun muassasani tanlang:**", # Matn o'zgartirildi
        reply_markup=create_delete_institution_keyboard(institutions_with_districts)
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
                f"✅ Muassasa **{institution.name}** muvaffaqiyatli o'chirildi.", # Matn o'zgartirildi
                reply_markup=None
            )
        else:
            await callback.message.edit_text("❌ Muassasa topilmadi.", reply_markup=None) # Matn o'zgartirildi

        db.close()
        await callback.answer()
    except Exception as e:
        await callback.message.answer(f"O'chirishda xatolik yuz berdi: {str(e)}") # Matn o'zgartirildi


@router.callback_query(F.data == "back_to_manage_data")
async def back_to_manage_data(callback: CallbackQuery):
    await callback.message.edit_text(
        "🏢 **Ma'lumotlarni boshqarish**\n\n" # Matn o'zgartirildi
        "Iltimos, harakatni tanlang:", # Matn o'zgartirildi
        reply_markup=create_admin_manage_data_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_admin_menu")
async def back_to_admin_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Qaytishing bilan, Administrator! 👋", # Matn o'zgartirildi
        reply_markup=create_admin_keyboard()
    )
    await callback.answer()


# (Qo'shilgan) Super Admin uchun Admin qo'shish
@router.message(F.text == "👑 Admin qo'shish")
async def super_admin_add_admin_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS: # Faqat Super Admin ID'lari
        await message.answer("❌ Sizda admin qo'shishga ruxsat yo'q.")
        return

    await message.answer(
        "👑 **Yangi admin qo'shish**\n\n"
        "Iltimos, yangi administratorning Telegram ID'sini kiriting:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Bekor qilish")]], resize_keyboard=True)
    )
    await state.set_state(SuperAdminAddAdmin.waiting_for_admin_telegram_id)


@router.message(StateFilter(SuperAdminAddAdmin.waiting_for_admin_telegram_id), F.text)
async def process_new_admin_telegram_id(message: Message, state: FSMContext):
    if message.text.lower() == "bekor qilish":
        await state.clear()
        await message.answer("Admin qo'shish bekor qilindi.", reply_markup=create_admin_keyboard())
        return

    try:
        telegram_id = int(message.text)
        db = SessionLocal()
        user = get_user_by_telegram_id(db, telegram_id)

        if user:
            # Agar foydalanuvchi mavjud bo'lsa, uning rolini o'zgartiramiz
            if user.role != 'admin':
                user.role = 'admin'
                db.commit()
                await message.answer(
                    f"✅ Foydalanuvchi **{user.full_name}** ({telegram_id}) muvaffaqiyatli admin roliga o'tkazildi!",
                    reply_markup=create_admin_keyboard()
                )
                try:
                    await bot.send_message(telegram_id, "🎉 Tabriklaymiz! Sizga administrator roli berildi.")
                except Exception as e:
                    logging.error(f"Yanggi adminga ({telegram_id}) xabar yuborishda xato: {e}")
            else:
                await message.answer("❌ Bu foydalanuvchi allaqachon administrator.", reply_markup=create_admin_keyboard())
            await state.clear()
        else:
            # Agar foydalanuvchi mavjud bo'lmasa, ma'lumotlarni yig'ib, yangi admin yaratamiz
            await state.update_data(new_admin_telegram_id=telegram_id)
            await message.answer(
                "Foydalanuvchi topilmadi. Iltimos, uning to'liq ismini kiriting:",
                reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Bekor qilish")]], resize_keyboard=True)
            )
            await state.set_state(SuperAdminAddAdmin.waiting_for_admin_full_name)
        db.close()

    except ValueError:
        await message.answer(
            "❌ Telegram ID faqat raqamlardan iborat bo'lishi kerak.\n"
            "Iltimos, qaytadan kiriting."
        )


@router.message(StateFilter(SuperAdminAddAdmin.waiting_for_admin_full_name), F.text)
async def process_new_admin_full_name(message: Message, state: FSMContext):
    if message.text.lower() == "bekor qilish":
        await state.clear()
        await message.answer("Admin qo'shish bekor qilindi.", reply_markup=create_admin_keyboard())
        return

    data = await state.get_data()
    telegram_id = data.get('new_admin_telegram_id')
    full_name = message.text

    db = SessionLocal()
    new_admin = create_user(
        db=db,
        telegram_id=telegram_id,
        region="Admin", # Adminlar uchun o'ziga xos region, district, institution
        district="Admin",
        institution="Admin",
        full_name=full_name,
        position="Administrator",
        role="admin"
    )
    db.close()
    await state.clear()

    await message.answer(
        f"✅ Yangi administrator **{new_admin.full_name}** ({new_admin.telegram_id}) muvaffaqiyatli qo'shildi!",
        reply_markup=create_admin_keyboard()
    )
    try:
        await bot.send_message(new_admin.telegram_id, "🎉 Tabriklaymiz! Siz tizimga administrator sifatida qo'shildingiz.")
    except Exception as e:
        logging.error(f"Yangi adminga ({new_admin.telegram_id}) xabar yuborishda xato: {e}")


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











