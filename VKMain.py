import os
import datetime
import random
from mpmath import mp
from dotenv import load_dotenv
import vk_api.vk_api
from vk_api.exceptions import ApiError
from vk_api.bot_longpoll import VkBotLongPoll
from vk_api.utils import get_random_id

load_dotenv()

##################################################################################
# передача значения количества недель, если хочеца запланировать посты несколько недель вперед +1# 
posts_amount=15
mp.dps = 61
deerday_chance = 12

class Server:
    def __init__(self, api_token, group_id, server_name="Empty"):
        # Даем серверу имя
        self.server_name = server_name
        self.group_id = group_id
        # Для Long Poll
        self.vk = vk_api.VkApi(token=api_token)
        # Для использования Long Poll API
        self.long_poll = VkBotLongPoll(self.vk, group_id)
        # Для вызова методов vk_api
        self.vk_api = self.vk.get_api()

    def send_post(self, owner_id, message=None, photo_id=None, from_group=1, publish_time=None):
        publish_date = int(publish_time.timestamp())
        # Если указан photo_id, формируем attachments
        attachments = None
        if photo_id:
            attachments = f"photo{owner_id}_{photo_id}"
        try:
            self.vk_api.wall.post(owner_id=owner_id, message=message, from_group=from_group, attachments=attachments, publish_date=publish_date)
            print(f"Запланирован пост на {publish_time.strftime('%Y-%m-%d %H:%M')}")
        except ApiError as e:
            print("Ошибка api. Возможно такой пост уже существует")

    def schedule_post_for_friday(self, base_message, photo_ids=None, posts_amount=4):
        
        
        # Определяем текущую дату и время
        now = datetime.datetime.now()
        
        
        # Определяем количество дней до ближайшей пятницы (пятница = 4)

        for i in range(0,posts_amount):
            days_until_friday = (4 - now.weekday() + 7) 
            if days_until_friday == 0:
                days_until_friday = 7
            # Вычисляем дату ближайшей пятницы в 12:00
            next_friday = now + datetime.timedelta(days=days_until_friday) + datetime.timedelta(days=7*i)
            
            publish_time = next_friday.replace(hour=10, minute=0, second=0, microsecond=0)
            # Преобразуем время публикации в формат Unix-времени

            day, month, year = next_friday.day, next_friday.month, next_friday.year
            pi_modifier = int(str(mp.pi)[14:][day])
            sum = day + month + year + pi_modifier
            rand_num = sum % deerday_chance
                        
            # Запланируем пост
            
            photo_id = photo_ids[0]

            if rand_num == min(5, deerday_chance-1):
                photo_id = photo_ids[1]
                print("БУДЕТ ЛОСЯТНИЦА")
                message = "ЛОСЯТНИЦА"
            else:
                message=base_message
            self.send_post(
                owner_id=-self.group_id,
                message=message,
                photo_id=photo_id,
                publish_time=publish_time
            )
            

# Инициализация сервера
server1 = Server(os.getenv('VK_TOKEN'), 216246042, "server1")

# Запланировать пост на ближайшую пятницу

server1.schedule_post_for_friday(" ", [f"457239023%2F7e9e639e2d0a98f606", f"457239146%2Fda0b72110b2bc850c3"], posts_amount=posts_amount)