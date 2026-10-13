"""The web installer (site/web-installer, site/assets/web-installer): its JavaScript tests (tests/web, run with
Node), and what can be checked about the pages without a browser - the three languages say the same things, every
sentence the code asks for exists, the pages load nothing from anywhere else, and the staged site carries the device
scripts the page pushes."""
import base64
import hashlib
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

WI = TOP / 'site/assets/web-installer'
PAGES = {'en': TOP / 'site/web-installer/index.html', 'tr': TOP / 'site/tr/web-installer/index.html',
         'zh': TOP / 'site/zh/web-installer/index.html'}
NODE = shutil.which('node')


def node_major():
    if not NODE:
        return 0
    try:
        return int(subprocess.run([NODE, '--version'], capture_output=True, text=True).stdout.strip().lstrip('v').split('.')[0])
    except ValueError:
        return 0


def strings():
    """{lang: {key: text}} out of strings.js (the file is three object literals of "key": "text" pairs)"""
    src = (WI / 'strings.js').read_text(encoding='utf-8')
    out = {}
    for lang in ('en', 'tr', 'zh'):
        body = src[src.index(f'const {lang} = {{'):]
        body = body[:body.index('\n};')]
        out[lang] = dict(re.findall(r'"([a-z0-9.-]+)":\s*"((?:[^"\\]|\\.)*)"', body))
    return out


@unittest.skipIf(node_major() < 20, 'Node.js 20 or newer is needed')
class JavaScript(unittest.TestCase):
    def test_node_tests(self):
        files = sorted(str(p) for p in (TOP / 'tests/web').glob('*.test.mjs'))
        self.assertGreaterEqual(len(files), 4)
        r = subprocess.run([NODE, '--test', '--test-timeout=120000'] + files, capture_output=True, text=True, timeout=900, cwd=TOP)
        # (the reporter depends on whether Node writes to a terminal: "not ok" lines of TAP, or the ✖ of spec)
        failed = '\n'.join(l for l in r.stdout.splitlines() if l.startswith(('not ok', '✖')) or 'Error' in l)[-6000:]
        self.assertEqual(r.returncode, 0, failed + r.stderr[-2000:])
        self.assertRegex(r.stdout, r'(?m)^(ℹ|#) fail 0$')
        self.assertRegex(r.stdout, r'(?m)^(ℹ|#) pass [1-9]\d')

    def test_every_module_parses(self):
        for f in sorted(WI.rglob('*.js')):
            r = subprocess.run([NODE, '--check', str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f'{f}: {r.stderr}')


class Strings(unittest.TestCase):
    def setUp(self):
        self.s = strings()

    def test_three_languages_same_keys_and_placeholders(self):
        en = self.s['en']
        self.assertGreater(len(en), 300)
        for lang in ('tr', 'zh'):
            self.assertEqual(sorted(set(en) - set(self.s[lang])), [], f'missing in {lang}')
            self.assertEqual(sorted(set(self.s[lang]) - set(en)), [], f'only in {lang}')
            for k, v in en.items():
                self.assertEqual(sorted(set(re.findall(r'\{\d\}', v))), sorted(set(re.findall(r'\{\d\}', self.s[lang][k]))), f'{lang} {k}')
                self.assertEqual(v.count('['), self.s[lang][k].count('['), f'{lang} {k}: the link brackets')
                if v:
                    self.assertTrue(self.s[lang][k].strip(), f'{lang} {k} is empty')

    def test_words_to_type_and_commands_are_not_translated(self):
        for lang, table in self.s.items():
            self.assertIn('sudo mu300-next-boot android && sudo reboot', table['done.back'], lang)
            self.assertIn('su -c mu300-linux', table['done.back'], lang)
            self.assertIn('{1}', table['erase.type'], lang)

    def test_every_sentence_the_code_asks_for_exists(self):
        en = self.s['en']
        app = (WI / 'app.js').read_text()
        asked = set(re.findall(r'(?<![A-Za-z.])t\("([a-z0-9-]+\.[a-z0-9.-]+)"', app)) | set(re.findall(r'[?:] "([a-z]+\.[a-z0-9.-]+)"', app))
        # a key that ends in a dot is a family, completed at run time: checked below
        # (an error's code is asked for as "err." + code)
        known = lambda k: k in en or 'err.' + k in en or any(e.startswith('err.' + k + '.') for e in en)
        self.assertEqual(sorted(k for k in asked if not k.endswith('.') and not known(k)), [])
        fam = {}
        flow = (WI / 'flow.js').read_text()
        device = (WI / 'device.js').read_text()
        web = (TOP / 'tools/android-web.sh').read_text()
        # the choices the wizard refuses, and what blocks an installation
        validate = flow[flow.index('export function validateChoices'):flow.index('export function dataNeeded')]
        fam['verr.'] = set(re.findall(r'code: "([a-z0-9-]+)"', validate)) | {'password-differs'}
        blockers = flow[flow.index('export function deviceBlockers'):flow.index('export function validPassword')]
        fam['blocker.'] = set(re.findall(r'"([a-z-]+)"', blockers)) - {'magisk'}
        # the device script's refusals and device.js's own
        fam['err.dev.'] = set(re.findall(r'\bfail ([a-z-]+)', web)) | set(re.findall(r'DeviceError\("([a-z-]+)"', device))
        fam['err.adb.'] = set(re.findall(r'AdbError\("([a-z-]+)"', ''.join(p.read_text() for p in (WI / 'adb').glob('*.js')))) - {'access'}
        fam['err.gh.'] = set(re.findall(r'GithubError\("([a-z-]+)"', (WI / 'github.js').read_text()))
        fam['step.'] = set(re.search(r'INSTALL_STEPS = \[(.*?)\]', flow).group(1).replace('"', '').split(', '))
        fam['ustep.'] = set(re.search(r'UNINSTALL_STEPS = \[(.*?)\]', flow).group(1).replace('"', '').split(', '))
        fam['urisk.'] = fam['ustep.']
        fam['risk.'] = {'nothing', 'systems', 'boot', 'arm', 'done'}
        fam['storage.verdict.'] = set(re.findall(r'verdict = "([a-z-]+)"', flow))
        fam['storage.seg.'] = set(re.findall(r'kind: "([a-z-]+)"', flow)) - {'gap'}
        fam['err.adb.access.'] = {'windows', 'linux', 'mac', 'android', 'other'}
        fam['lock.'] = fam['explain.lock.'] = {'unlocked', 'locked', 'unknown'}
        fam['sys.about.'] = {'ubuntu-24.04', 'ubuntu-26.04', 'openwrt', 'openwrt-luci'}
        fam['kernel.'] = {'5.4', '6.18', '7.2'}
        fam['job.kind.'] = {'plan', 'install', 'uninstall'}
        fam['review.pw.'] = {'given', 'kept', 'image'}
        fam['wizard.step.'] = set(re.search(r'WSTEPS = \[(.*?)\]', app).group(1).replace('"', '').split(', '))
        fam['storage.sd.'] = {'yes', 'no', 'foreign', 'none', 'small'}
        for prefix, names in fam.items():
            self.assertTrue(names, prefix)
            for n in names:
                self.assertTrue(prefix + n in en, prefix + n)
        # the steps the device scripts name are the ones the page lists
        installer = (TOP / 'android/magisk/installer/mu300-install.sh').read_text()
        self.assertEqual(set(re.search(r"\nSTEPS='(.*?)'", installer).group(1).split()), fam['step.'])
        self.assertEqual(set(re.search(r"\nUNSTEPS='(.*?)'", web).group(1).split()), fam['ustep.'])


class Pages(unittest.TestCase):
    THEME = 'try{var t=localStorage.getItem("mu300-theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}'

    def test_three_pages_with_the_same_structure(self):
        ids = None
        for lang, p in PAGES.items():
            s = p.read_text(encoding='utf-8')
            self.assertIn(f'<html lang="{"zh-Hans" if lang == "zh" else lang}">', s)
            self.assertIn('id="wi-app" hidden', s)
            self.assertIn('<noscript>', s)
            self.assertIn('class="callout warn"', s)                     # experimental, and the supported way named
            self.assertIn('../install/', s)
            self.assertIn('web-installer/app.js', s)
            self.assertIn('aria-current="page"', s)
            self.assertEqual(s.count('https://t.me/mu300project'), 1, f'{lang}: the Telegram line of the footer')
            found = re.findall(r'<h2 id="([a-z-]+)"', s)
            ids = ids or found
            self.assertEqual(found, ids, lang)
            for l2 in ('', 'tr/', 'zh/'):
                self.assertIn(f'href="https://kaandikec.com/mu300-linux/{l2}web-installer/"', s)

    def test_the_page_loads_nothing_from_elsewhere_and_says_so_in_a_policy(self):
        want = base64.b64encode(hashlib.sha256(self.THEME.encode()).digest()).decode()
        for lang, p in PAGES.items():
            s = p.read_text(encoding='utf-8')
            csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', s).group(1)
            self.assertIn("default-src 'none'", csp)
            self.assertIn(f"script-src 'self' 'sha256-{want}'", csp)
            self.assertIn("connect-src 'self' https://api.github.com", csp)
            self.assertNotIn('unsafe', csp)
            self.assertIn(f'<script>{self.THEME}</script>', s)
            # every script, stylesheet and image is this site's own (relative), no web fonts
            for tag, attr in (('script', 'src'), ('img', 'src')):
                for m in re.findall(rf'<{tag}\b[^>]*\b{attr}="([^"]+)"', s):
                    self.assertFalse(m.startswith(('http:', 'https:', '//')), m)
            for m in re.findall(r'<link\b[^>]*rel="(?:stylesheet|preconnect|preload)"[^>]*href="([^"]+)"', s):
                self.assertFalse(m.startswith(('http:', 'https:', '//')), m)
            self.assertNotIn('fonts.googleapis.com', s)
            self.assertNotIn(' style="', s.split('<main')[1].split('</main>')[0])   # the policy forbids inline styles

    def test_the_code_talks_to_github_and_this_site_only_and_never_builds_markup(self):
        hosts = set()
        for f in sorted(WI.rglob('*.js')):
            src = f.read_text(encoding='utf-8')
            for forbidden in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function', 'console.',
                              'XMLHttpRequest', 'WebSocket', 'sendBeacon', 'localStorage', 'sessionStorage', 'document.cookie'):
                self.assertNotIn(forbidden, src, f'{f.name}: {forbidden}')
            hosts |= set(re.findall(r'https://([a-z0-9.-]+)/', src))
        self.assertEqual(hosts - {'api.github.com', 'github.com', 't.me'}, set(), hosts)
        # fetch() is called in two places only: the release list and this site's own device scripts
        fetches = [f.name for f in sorted(WI.rglob('*.js')) for _ in re.findall(r'\bfetch(?:Impl)?\(', f.read_text())]
        self.assertEqual(sorted(set(fetches)), ['app.js', 'github.js'])
        self.assertNotIn('getprop ro.serialno', ''.join(f.read_text() for f in WI.rglob('*.js')))

    def test_linked_from_the_navigation_the_install_page_and_the_sitemap(self):
        for p in sorted((TOP / 'site').rglob('index.html')):
            self.assertRegex(p.read_text(encoding='utf-8'), r'<a href="[^"]*web-installer/"', str(p))
        for pre in ('', 'tr/', 'zh/'):
            self.assertIn('<a href="../web-installer/">', (TOP / 'site' / pre / 'install/index.html').read_text(encoding='utf-8'))
        self.assertEqual((TOP / 'site/sitemap.xml').read_text().count('web-installer/'), 5)

    def test_the_staged_site_has_the_device_scripts_with_their_checksums(self):
        out = Path(tempfile.mkdtemp(prefix='mu300-site-'))
        try:
            r = subprocess.run(['sh', str(TOP / 'site/tools/stage.sh'), str(out)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            dev = out / 'assets/web-installer/device'
            names = re.search(r'DEVICE_FILES = \[(.*?)\]', (WI / 'device.js').read_text()).group(1).replace('"', '').split(', ')
            sums = dict(reversed(l.split()) for l in (dev / 'SHA256SUMS').read_text().splitlines())
            self.assertEqual(sorted(sums), sorted(names))
            for n in names:
                self.assertEqual(hashlib.sha256((dev / n).read_bytes()).hexdigest(), sums[n], n)
            self.assertEqual((dev / 'android-web.sh').read_bytes(), (TOP / 'tools/android-web.sh').read_bytes())
            self.assertFalse((out / 'tools').exists())
            self.assertTrue((out / 'web-installer/index.html').is_file())
            self.assertTrue((out / 'index.html').is_file())
            # staging into the site itself is refused
            r = subprocess.run(['sh', str(TOP / 'site/tools/stage.sh'), str(TOP / 'site/x')], capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_the_pages_workflow_stages_with_that_script(self):
        y = (TOP / '.github/workflows/pages.yml').read_text()
        self.assertIn('sh site/tools/stage.sh _site', y)
        for path in ("'tools/android-web.sh'", "'tools/storage.sh'", "'android/magisk/mu300-linux-switch/switch.sh'"):
            self.assertIn(path, y)


if __name__ == '__main__':
    unittest.main()
