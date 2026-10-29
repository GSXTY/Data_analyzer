"""Build a self-contained Windows ZIP; explicit source/assets, no user data."""
from pathlib import Path
import os
import subprocess
import sys
import shutil
import hashlib
import importlib.metadata
from app_version import APP_VERSION

ROOT=Path(__file__).resolve().parent
def main():
    if sys.platform!='win32':raise SystemExit('Build this bundle on Windows.')
    env=os.environ.copy()
    env['PYINSTALLER_CONFIG_DIR']=str(ROOT/'build'/'pyinstaller-cache')
    if (ROOT/'.build-deps').exists():env['PYTHONPATH']=str(ROOT/'.build-deps')
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--console','--name','RespiratoryViewer',
             '--distpath',str(ROOT/'dist'),'--workpath',str(ROOT/'build'),
             '--specpath',str(ROOT/'build'),'--exclude-module','matplotlib','--exclude-module','scipy',
             '--exclude-module','IPython','--exclude-module','pytest','--exclude-module','torch',
             '--exclude-module','tkinter']
    for name in ('index.html','viewer.js','annotation-model.js'):command+=['--add-data',f'{ROOT/name}:.']
    subprocess.run(command+[str(ROOT/'viewer.py')],cwd=ROOT,env=env,check=True)
    bundle=ROOT/'dist'/'RespiratoryViewer'
    shutil.copyfile(ROOT/'USER_GUIDE.md',bundle/'使用说明.txt')
    licenses=bundle/'THIRD_PARTY_LICENSES';licenses.mkdir(exist_ok=True)
    distributions={d.metadata['Name'].lower():d for d in importlib.metadata.distributions(path=[str(ROOT/'.build-deps'),*sys.path]) if d.metadata.get('Name')}
    for name in ('numpy','pandas','python-dateutil','six','tzdata','pyinstaller'):
        distribution=distributions.get(name)
        if distribution is None:continue
        for entry in distribution.files or []:
            if any(word in entry.name.lower() for word in ('license','copying','notice')):
                source=distribution.locate_file(entry)
                if not source.is_file():continue
                parts=entry.parts
                relative=Path(*parts[1:]) if len(parts)>1 else Path(entry.name)
                destination=licenses/name/relative
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,destination)
    for name in ('LICENSE.txt','LICENSE'):
        source=Path(sys.base_prefix)/name
        if source.is_file():shutil.copyfile(source,licenses/('Python-'+name))
    output=ROOT/'release';output.mkdir(exist_ok=True)
    path=Path(shutil.make_archive(str(output/f'RespiratoryViewer-{APP_VERSION}-windows-x64'),'zip',ROOT/'dist','RespiratoryViewer'))
    (output/(path.name+'.sha256')).write_text(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n',encoding='ascii')
    print(path)
if __name__=='__main__':main()
