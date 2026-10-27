"""RSS-лента dantpro.ru для Дзена. Запускается GitHub Actions каждый час.
Берёт из Тильды опубликованные статьи блога (/blog/...) в новом дизайне (Vibe-блок T1370),
чистит текст до HTML, который понимает Дзен, и пишет docs/dzen.xml. Ключ Tilda MCP — в переменной TILDA_MCP_KEY.
Статьи из state.json["drafts"] уходят в Дзен черновиками, все новые публикуются сразу."""
import os,re,json,html,time,urllib.request,urllib.error
from html.parser import HTMLParser
from email.utils import format_datetime
from datetime import datetime,timezone,timedelta
P=8277548; SITE='https://dantpro.ru'; DIR=os.path.dirname(os.path.abspath(__file__))
STATE=os.path.join(DIR,'state.json'); OUT=os.path.join(DIR,'docs','dzen.xml'); MAX_ITEMS=30
TZ=timezone(timedelta(hours=5))

class Tilda:
    def __init__(s,key):
        s.key=key; s.sid=None; s.i=0; s.last=0
        s.rpc('initialize',{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"dant-dzen","version":"1"}})
        s.rpc('notifications/initialized',None,True)
    def rpc(s,method,params,notify=False):
        body={"jsonrpc":"2.0","method":method}
        if params is not None: body['params']=params
        if not notify: s.i+=1; body['id']=s.i
        h={'Authorization':s.key if s.key.lower().startswith('bearer') else 'Bearer '+s.key,'Content-Type':'application/json',
           'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18'}
        if s.sid: h['Mcp-Session-Id']=s.sid
        for a in range(8):
            time.sleep(max(0,1.2-(time.time()-s.last))); s.last=time.time()
            try: r=urllib.request.urlopen(urllib.request.Request('https://tilda.ru/api/mcp/',json.dumps(body).encode(),h),timeout=120); break
            except urllib.error.HTTPError as e:
                if e.code==429 and a<7: time.sleep(5*(a+1)); continue
                raise RuntimeError('Tilda HTTP %s %s'%(e.code,e.read().decode()[:300]))
        s.sid=r.headers.get('Mcp-Session-Id') or s.sid; t=r.read().decode()
        if notify: return
        if t.startswith('event') or t.startswith('data:') or '\ndata:' in t: t=[l[5:].strip() for l in t.splitlines() if l.startswith('data:')][-1]
        return json.loads(t)
    def call(s,name,**args):
        for a in range(4):
            try: r=s.rpc('tools/call',{'name':name,'arguments':args}); break
            except Exception:
                if a==3: raise
                time.sleep(2**a)
        if 'error' in r: raise RuntimeError(r['error'])
        res=r['result']; t=''.join(c.get('text','') for c in res.get('content',[]))
        if res.get('isError'): raise RuntimeError(t[:500])
        try: return json.loads(t)
        except Exception: return t

KEEP={'h2':'h2','h3':'h3','p':'p','li':'li','ul':'ul','ol':'ol','blockquote':'blockquote','b':'b','strong':'b','i':'i','em':'i','a':'a'}
VOID=('img','br','input','meta','link','source','hr')
class Clean(HTMLParser):
    def __init__(s):
        super().__init__(convert_charrefs=True); s.o=[]; s.skip=0; s.imgs=[]; s.h1=[]; s.inh1=0
    def handle_starttag(s,tag,a):
        a=dict(a); cls=a.get('class','') or ''
        if s.skip: s.skip+= tag not in VOID; return
        if tag in('style','script','form','button','svg','noscript') or any(k in cls for k in('form-wrap','__actions','eyebrow','card-num','__stats')):
            s.skip= 0 if tag in VOID else 1; return
        if tag=='h1': s.inh1=1; return
        if tag=='img':
            src=a.get('src','') or a.get('data-original','')
            if src and '/lib/icons/' not in src: s.imgs.append(src); s.o.append('<img src="%s" alt="%s">'%(src,html.escape(a.get('alt',''),quote=True)))
            return
        if tag in KEEP and not s.inh1:
            if tag=='a':
                h=a.get('href','')
                if not h or h.startswith('#'): s.o.append('<a>'); return
                if h.startswith('/'): h=SITE+h
                s.o.append('<a href="%s">'%html.escape(h,quote=True)); return
            s.o.append('<%s>'%KEEP[tag])
    def handle_endtag(s,tag):
        if s.skip:
            if tag not in VOID: s.skip-=1
            return
        if tag=='h1': s.inh1=0; return
        if tag in KEEP and not s.inh1: s.o.append('</%s>'%KEEP[tag])
    def handle_data(s,d):
        if s.skip: return
        (s.h1 if s.inh1 else s.o).append(d if s.inh1 else html.escape(d,quote=False))
def strip(x): return re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>','',x)).replace('\xa0',' ')).strip()
def tidy(h):
    h=re.sub(r'<a>(.*?)</a>',r'\1',h.replace('\xa0',' ')); h=re.sub(r'\s+',' ',h)
    h=re.sub(r'>\s+<','><',h)
    for _ in range(3): h=re.sub(r'<(\w+)>\s*</\1>','',h)
    return re.sub(r'</a>(?=[^\s.,;:!?»)<])','</a> ',h).strip()

def article(t,page,settings):
    pid=int(page['id']); recs=t.call('get_page_records',pageid=pid,projectid=P)
    vib=[r for r in recs if str(r.get('tplid'))=='1370']
    if not vib: return None
    code=t.call('edit_record',pageid=pid,recordid=int(vib[0]['recordid']),projectid=P,tab='content')['record']['code']
    c=Clean(); c.feed(code)
    title=re.sub(r'\s+',' ',''.join(c.h1)).replace('\xa0',' ').strip()
    title=re.sub(r'\s*—\s*',': ',title,count=1).strip(' :') or settings.get('title','').split('|')[0].strip()
    title=title[:1].upper()+title[1:]
    body=tidy(''.join(c.o))
    stats=re.findall(r'stat-value">(.*?)</div>\s*<div class="vb-a2s__stat-label">(.*?)</div>',code,re.S)
    if stats:
        e=body.find('</p>')+4; body=body[:e]+'<ul>'+''.join('<li><b>%s</b> — %s</li>'%(html.escape(strip(v)),html.escape(strip(l))) for v,l in stats)+'</ul>'+body[e:]
    link='%s/%s'%(SITE,page['alias'])
    body+=('<p>Записаться на консультацию: <a href="tel:+73432261033">+7 (343) 226-10-33</a>, <a href="%s">dantpro.ru</a>. '
           'Подробнее — <a href="%s">в статье на сайте клиники «Дант»</a>.</p>')%(SITE,link)
    return dict(title=title,link=link,hero=c.imgs[0] if c.imgs else '',descr=(settings.get('descr') or settings.get('meta_descr') or '').strip(),
                body=body,chars=len(strip(body)))

def main():
    st=json.load(open(STATE)); st.setdefault('items',{})
    t=Tilda(os.environ['TILDA_MCP_KEY'])
    pages=t.call('get_pages',projectid=P); pages=pages if isinstance(pages,list) else pages.get('pages',[])
    cutoff=int(st['cutoff']); want=set(st['drafts'])
    blog={p['alias'].split('/',1)[1]:p for p in pages if str(p.get('alias','')).startswith('blog/') and not p.get('trash')}
    changed=False
    for slug,p in blog.items():
        pub=int(p.get('published') or 0)
        if slug not in want and pub<cutoff: continue
        if slug not in want and not pub: continue
        old=st['items'].get(slug)
        if old and old.get('changed')==p.get('changed'): continue
        if old and old.get('first_seen',0)<time.time()-7*86400: continue   # Дзен не обновляет материалы старше 7 дней
        s=t.call('get_page_settings',pageid=int(p['id']),projectid=P); s=s.get('page',s)
        if s.get('norss')=='y': continue
        a=article(t,p,s)
        if not a or a['chars']<300: print('skip',slug,'(нет блока T1370 или мало текста)'); continue
        a.update(slug=slug,changed=p.get('changed'),draft=slug in want,
                 date=(old or {}).get('date') or format_datetime(datetime.fromtimestamp(pub or time.time(),TZ)),
                 first_seen=(old or {}).get('first_seen') or int(time.time()))
        st['items'][slug]=a; changed=True; print('item',slug,a['chars'],'зн.')
    for slug in list(st['items']):
        if slug not in blog and slug not in want: del st['items'][slug]; changed=True; print('removed',slug)
    items=sorted(st['items'].values(),key=lambda x:x['first_seen'],reverse=True)[:MAX_ITEMS]
    x=['<?xml version="1.0" encoding="UTF-8"?>',
       '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:media="http://search.yahoo.com/mrss/">',
       '<channel><title>Стоматология «Дант»</title><link>%s</link><description>Статьи стоматологии «Дант» в Екатеринбурге</description><language>ru</language>'%SITE]
    for it in items:
        cats=(['native-draft'] if it['draft'] else [])+['format-article','index','comment-all']
        x.append('<item><title>%s</title><link>%s</link><guid>%s</guid><pubDate>%s</pubDate><media:rating scheme="urn:simple">nonadult</media:rating>%s'
                 '<enclosure url="%s" type="image/jpeg"/><description><![CDATA[%s]]></description><content:encoded><![CDATA[%s]]></content:encoded></item>'
                 %(html.escape(it['title']),it['link'],it['link'],it['date'],''.join('<category>%s</category>'%c for c in cats),it['hero'],it['descr'],it['body']))
    x.append('</channel></rss>')
    os.makedirs(os.path.dirname(OUT),exist_ok=True); new='\n'.join(x)
    if not os.path.exists(OUT) or open(OUT).read()!=new: open(OUT,'w').write(new); changed=True
    json.dump(st,open(STATE,'w'),ensure_ascii=False,indent=1)
    print('items in feed:',len(items),'| changed:',changed)
if __name__=='__main__': main()
