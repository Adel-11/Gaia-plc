import openpyxl, secrets, re
UP='/root/.claude/uploads/6a797342-3904-5e09-a0b5-85d521428fea/'
src=open(UP+'107d6763-SCADA_V1-devices_1.csv','rb').read().decode('utf-8')
assert '\r' not in src and src.endswith('\n\n\n')
DEV='d_abeaaea7-b8914e5b'
lines=src.split('\n')
tag_lines=[l for l in lines if l.startswith('T@,'+DEV)]
ncomma=tag_lines[0].count(',')
exist={(l.split(',')[6],l.split(',')[7]) for l in tag_lines if not l.split(',')[3].lower().startswith('test')}      # (memaddress,address)
exist_names={l.split(',')[3] for l in tag_lines}
ids=set(re.findall(r't_[0-9a-f]{8}-[0-9a-f]{8}',src))
def newid():
    while True:
        i='t_'+secrets.token_hex(4)+'-'+secrets.token_hex(4)
        if i not in ids: ids.add(i); return i
wb=openpyxl.load_workbook(UP+'5663c61b-GAIA-ELEC-1451-V1_-_IO_List.xlsm')
SHEETS=[('SCADA-Coils','000000',0,'Bool'),('SCADA-Discrete Inputs','100000',10000,'Bool'),
        ('SCADA-Input Registers','300000',0,None),('SCADA-Holding Registers','400000',0,None)]
out=[];skipped=[];report=[]
for sh,mem,off,typ in SHEETS:
    rows=[r for r in wb[sh].iter_rows(min_row=2,values_only=True) if r[0]]
    i=0
    while i<len(rows):
        r=rows[i]
        pair = typ is None and i+1<len(rows) and rows[i+1][0]==r[0]
        t = typ or ('Float32MLE' if pair else 'UInt16')
        addr=int(r[3])-off
        name=r[1]
        if (mem,str(addr)) in exist:
            skipped.append((sh,name,mem,addr))
        else:
            assert name, (sh,r)
            assert not re.search(r'[,~"\n]',name) and name==name.strip() and name not in exist_names, name
            exist_names.add(name)
            line=f'T@,{DEV},{newid()},{name},,{t},{mem},{addr},1,,,,,,,,false,60,false,false,'
            assert line.count(',')==ncomma
            out.append(line); report.append((sh,name,t,mem,addr))
        i+= 2 if pair else 1
# insertion: right after last existing tag line, keep everything else byte-identical
marker=tag_lines[-1]+'\n'
pos=src.index(marker)+len(marker)
new=src[:pos]+''.join(l+'\n' for l in out)+src[pos:]
open('/home/claude/work/SCADA_V1-devices_complet.csv','wb').write(new.encode('utf-8'))
print('existants:',len(tag_lines),'| ajoutes:',len(out),'| ignores (deja presents):',skipped)
from collections import Counter
print(Counter((s,t) for s,_,t,_,_ in report))
import json; json.dump(report,open('/home/claude/work/report.json','w'))
