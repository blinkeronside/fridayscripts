from mpmath import mp
from datetime import datetime

from datetime import timedelta
mp.dps = 61

time = datetime.now()

date2 = datetime.now()+timedelta(days=1)
print(f"FFFFFFFFFFFF{date2}" )

day, month, year = time.day, time.month, time.year
pi_modifier = int(str(mp.pi)[14:][day])
coeff = 20
sum = day + month + year + pi_modifier
rand_num = sum % coeff
if rand_num==5:
    print("Лосятница")