import asyncio,json,sqlite3,time,uuid,logging,os
from decimal import Decimal
import aiohttp
from aiogram import Bot,Dispatcher,F
from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton,LabeledPrice,PreCheckoutQuery
from aiogram.filters import CommandStart

logging.basicConfig(level=logging.INFO)
C=json.load(open('config.json',encoding='utf-8')); BOT=Bot(C['bot_token']); DP=Dispatcher(); DB='database.sqlite3'; XR=C.get('xrocket_api_key',''); ADM=set(map(int,C.get('admin_ids',[]))); PRODUCTS={str(p['id']):p for p in C.get('products',[])}; API='https://pay.api.xrocket.exchange'

def conn():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
 with conn() as c:
  c.execute('CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,requests INTEGER DEFAULT 0,created INTEGER)')
  c.execute('CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,user_id INTEGER,product_id TEXT,method TEXT,amount TEXT,asset TEXT,status TEXT,xid TEXT,charge TEXT,delivered INTEGER DEFAULT 0,created INTEGER)')
init()

def user(u):
 with conn() as c:
  c.execute('INSERT OR IGNORE INTO users VALUES(?,?,?,?,?)',(u.id,u.username or '',u.first_name or '',0,int(time.time())))
  c.execute('UPDATE users SET username=?,first_name=? WHERE id=?',(u.username or '',u.first_name or '',u.id))

def kb(uid):
 r=[[InlineKeyboardButton(text='🛍 Магазин',callback_data='shop'),InlineKeyboardButton(text='👤 Профиль',callback_data='profile')],[InlineKeyboardButton(text='📦 Покупки',callback_data='orders'),InlineKeyboardButton(text='💬 Поддержка',callback_data='support')]]
 if uid in ADM:r.append([InlineKeyboardButton(text='👨‍💼 Админка',callback_data='admin')])
 return InlineKeyboardMarkup(inline_keyboard=r)

def back(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='◀️ Назад',callback_data='home')]])

def products_kb():
 r=[[InlineKeyboardButton(text=f"🛒 {p['name']} — {p['price_stars']} ⭐",callback_data=f'p:{p["id"]}')] for p in PRODUCTS.values()];r.append([InlineKeyboardButton(text='◀️ Назад',callback_data='home')]);return InlineKeyboardMarkup(inline_keyboard=r)

@DP.message(CommandStart())
async def start(m):
 user(m.from_user); await m.answer(f"✨ <b>{C.get('store',{}).get('name','Premium Store')}</b>\n\nВыберите раздел:",reply_markup=kb(m.from_user.id))

@DP.callback_query(F.data=='home')
async def home(q): await q.answer(); await q.message.edit_text('✨ <b>Главное меню</b>\n\nВыберите раздел:',reply_markup=kb(q.from_user.id))

@DP.callback_query(F.data=='shop')
async def shop(q): await q.answer(); await q.message.edit_text('🛍 <b>Магазин</b>\n\nВыберите товар:',reply_markup=products_kb() if PRODUCTS else back())

@DP.callback_query(F.data.startswith('p:'))
async def product(q):
 await q.answer(); p=PRODUCTS.get(q.data[2:])
 if not p:return await q.message.answer('❌ Товар не найден.')
 await q.message.edit_text(f"🛍 <b>{p['name']}</b>\n\n{p.get('description','')}\n\n🚀 {p['price_xrocket']} {p.get('xrocket_asset','USDT')}\n⭐ {p['price_stars']} XTR",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🚀 xRocket',callback_data=f'x:{p["id"]}')],[InlineKeyboardButton(text='⭐ Stars',callback_data=f's:{p["id"]}')],[InlineKeyboardButton(text='◀️ Назад',callback_data='shop')]]))

async def xr(method,url,**kw):
 h={'Authorization':f'Bearer {XR}','Accept':'application/json','Content-Type':'application/json'}
 async with aiohttp.ClientSession() as s:
  async with getattr(s,method)(url,headers=h,timeout=15,**kw) as r:
   d=await r.json();
   if r.status>=400:raise RuntimeError(d)
   return d

@DP.callback_query(F.data.startswith('x:'))
async def xpay(q):
 await q.answer(); pid=q.data[2:];p=PRODUCTS.get(pid)
 if not p or not XR:return await q.message.answer('❌ xRocket не настроен или товар не найден.')
 try:d=await xr('post',API+'/api/v1/invoices',json={'amount':str(p['price_xrocket']),'asset':p.get('xrocket_asset','USDT'),'description':p['name'],'clientInvoiceId':uuid.uuid4().hex})
 except Exception as e:return await q.message.answer('❌ Ошибка создания счёта.')
 xid=str(d.get('invoiceId') or d.get('id')); url=d.get('link') or d.get('url') or d.get('payUrl') or d.get('paymentUrl')
 if not xid or not url:return await q.message.answer('❌ xRocket вернул некорректный счёт.')
 oid=uuid.uuid4().hex
 with conn() as c:c.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?)',(oid,q.from_user.id,pid,'xrocket',str(p['price_xrocket']),p.get('xrocket_asset','USDT'),'pending',xid,None,0,int(time.time())))
 await q.message.answer(f"🧾 <b>Счёт создан</b>\n\n{p['name']}\n{p['price_xrocket']} {p.get('xrocket_asset','USDT')}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🚀 Оплатить',url=url)],[InlineKeyboardButton(text='🔄 Проверить',callback_data=f'cx:{oid}')]]))
 asyncio.create_task(waitx(oid))

def paid(d,a,asset):
 if str(d.get('status','')).lower() not in ('paid','completed','success'):return False
 if str(d.get('asset') or d.get('currency') or '').upper()!=asset.upper():return False
 try:return Decimal(str(d.get('amount') or d.get('paidAmount') or d.get('amountPaid')))>=Decimal(str(a))
 except:return False

async def deliver(o):
 with conn() as c:
  r=c.execute('UPDATE orders SET status="delivered",delivered=1 WHERE id=? AND delivered=0',(o['id'],));
  if r.rowcount!=1:return
  p=PRODUCTS.get(o['product_id']);q=int(p.get('quantity',1)) if p else 0
  if p and p.get('type','requests')=='requests':c.execute('UPDATE users SET requests=requests+? WHERE id=?',(q,o['user_id']))
 if p and p.get('type','requests')=='requests':await BOT.send_message(o['user_id'],f'✅ Оплата подтверждена!\n\n🎁 Выдано запросов: <b>{q}</b>')
 else:await BOT.send_message(o['user_id'],f"✅ Оплата подтверждена!\n\n🎁 {p['name'] if p else 'Товар'}")

async def waitx(oid):
 for _ in range(360):
  with conn() as c:o=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
  if not o or o['delivered']:return
  try:
   d=await xr('get',API+'/api/v1/invoice',params={'invoiceId':o['xid']})
   if paid(d,o['amount'],o['asset']):return await deliver(o)
  except Exception:logging.exception('xRocket check')
  await asyncio.sleep(5)

@DP.callback_query(F.data.startswith('cx:'))
async def checkx(q):
 await q.answer('Проверяю...');
 with conn() as c:o=c.execute('SELECT * FROM orders WHERE id=?',(q.data[3:],)).fetchone()
 if not o or o['user_id']!=q.from_user.id:return await q.message.answer('❌ Заказ не найден.')
 if o['delivered']:return await q.message.answer('✅ Уже выдан.')
 try:
  d=await xr('get',API+'/api/v1/invoice',params={'invoiceId':o['xid']})
  if paid(d,o['amount'],o['asset']):await deliver(o)
  else:await q.message.answer('⏳ Оплата ещё не подтверждена.')
 except:await q.message.answer('❌ Ошибка проверки.')

@DP.callback_query(F.data.startswith('s:'))
async def spay(q):
 await q.answer();p=PRODUCTS.get(q.data[2:]);
 if not p:return await q.message.answer('❌ Товар не найден.')
 oid=uuid.uuid4().hex
 with conn() as c:c.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?)',(oid,q.from_user.id,p['id'],'stars',str(p['price_stars']),'XTR','pending',None,None,0,int(time.time())))
 await BOT.send_invoice(q.message.chat.id,p['name'],p.get('description',p['name']),f'stars:{oid}','XTR',[LabeledPrice(label=p['name'],amount=int(p['price_stars']))])

@DP.pre_checkout_query()
async def pre(q:PreCheckoutQuery):
 oid=q.invoice_payload.split(':',1)[1] if q.invoice_payload.startswith('stars:') else ''
 with conn() as c:o=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
 await q.answer(ok=bool(o and o['status']=='pending' and q.currency=='XTR' and q.total_amount==int(o['amount'])),error_message=None if o else 'Заказ недействителен.')

@DP.message(F.successful_payment)
async def success(m):
 p=m.successful_payment
 if not p.invoice_payload.startswith('stars:'):return
 oid=p.invoice_payload[6:]
 with conn() as c:o=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
 if not o or o['user_id']!=m.from_user.id or p.currency!='XTR' or p.total_amount!=int(o['amount']) or o['delivered']:return
 with conn() as c:c.execute('UPDATE orders SET status="paid",charge=? WHERE id=?',(p.telegram_payment_charge_id,oid))
 await deliver(o)

@DP.callback_query(F.data=='profile')
async def profile(q):
 await q.answer();user(q.from_user)
 with conn() as c:u=c.execute('SELECT * FROM users WHERE id=?',(q.from_user.id,)).fetchone();n=c.execute('SELECT COUNT(*) n FROM orders WHERE user_id=? AND status="delivered"',(q.from_user.id,)).fetchone()['n']
 await q.message.edit_text(f"👤 <b>Профиль</b>\n\n🆔 <code>{q.from_user.id}</code>\n📨 Запросов: <b>{u['requests']}</b>\n📦 Покупок: <b>{n}</b>",reply_markup=back())

@DP.callback_query(F.data=='orders')
async def orders(q):
 await q.answer()
 with conn() as c:r=c.execute('SELECT * FROM orders WHERE user_id=? ORDER BY created DESC LIMIT 15',(q.from_user.id,)).fetchall()
 text='📦 <b>Мои покупки</b>\n\n'+('\n'.join(f"• {PRODUCTS.get(x['product_id'],{'name':x['product_id']})['name']} — {x['status']}" for x in r) if r else 'Покупок нет.')
 await q.message.edit_text(text,reply_markup=back())

@DP.callback_query(F.data=='support')
async def support(q):await q.answer();await q.message.edit_text('💬 <b>Поддержка</b>\n\nОбратитесь к администратору.',reply_markup=back())

@DP.callback_query(F.data=='admin')
async def admin(q):
 if q.from_user.id not in ADM:return await q.answer('Нет доступа',show_alert=True)
 await q.answer();await q.message.edit_text('👨‍💼 <b>Админ-панель</b>\n\nВыберите действие:',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='👥 Пользователи',callback_data='au')],[InlineKeyboardButton(text='📊 Статистика',callback_data='ast')],[InlineKeyboardButton(text='➕ Выдать запросы',callback_data='aa')],[InlineKeyboardButton(text='➖ Забрать запросы',callback_data='as')],[InlineKeyboardButton(text='📢 Рассылка',callback_data='ab')],[InlineKeyboardButton(text='◀️ Назад',callback_data='home')]]))

@DP.callback_query(F.data=='au')
async def au(q):
 if q.from_user.id not in ADM:return
 await q.answer()
 with conn() as c:r=c.execute('SELECT id,username,requests FROM users ORDER BY created DESC LIMIT 50').fetchall()
 await q.message.edit_text('👥 <b>Пользователи</b>\n\n'+'\n'.join(f"• <code>{x['id']}</code> @{x['username']} — {x['requests']} запросов" for x in r) if r else 'Пользователей нет.',reply_markup=back())

@DP.callback_query(F.data=='ast')
async def ast(q):
 if q.from_user.id not in ADM:return
 await q.answer()
 with conn() as c:u=c.execute('SELECT COUNT(*) n FROM users').fetchone()['n'];o=c.execute('SELECT COUNT(*) n FROM orders').fetchone()['n'];d=c.execute('SELECT COUNT(*) n FROM orders WHERE status="delivered"').fetchone()['n']
 await q.message.edit_text(f'📊 <b>Статистика</b>\n\n👥 Пользователей: {u}\n🧾 Заказов: {o}\n✅ Выдано: {d}',reply_markup=back())

async def main():
 await DP.start_polling(BOT)

if __name__=='__main__':asyncio.run(main())
