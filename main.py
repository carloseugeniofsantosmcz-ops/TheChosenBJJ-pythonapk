"""The Chosen BJJ - cliente Android nativo em Python/Kivy.
O Supabase permanece como fonte de dados; RLS define permissões.
"""
import datetime
import json
import threading
import re
import time
from urllib.parse import quote, urlparse
import webbrowser
import logging
import requests
from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.checkbox import CheckBox
from kivy.uix.popup import Popup
from kivy.core.window import Window

from config import SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY

ORANGE = (0.91, .35, .07, 1)
DARK = (.10, .11, .14, 1)
WHITE = (1, 1, 1, 1)

def valid_cpf(value):
    digits = re.sub(r'\D', '', str(value))
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for length in (9, 10):
        total = sum(int(digits[i]) * (length + 1 - i) for i in range(length))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if check != int(digits[length]):
            return False
    return True


class ApiError(Exception):
    pass

class Supabase:
    def __init__(self):
        self.token = None
        self.user_id = None
        self.refresh_token = None
        self.expires_at = 0
        self.session = requests.Session()
        # Impede que tokens sejam enviados em redirecionamentos HTTP externos.
        self.session.max_redirects = 0
        self._auth_lock = threading.RLock()

    def _store_auth(self, result):
        self.token = result['access_token']
        self.refresh_token = result.get('refresh_token')
        self.expires_at = time.time() + int(result.get('expires_in', 3600))
        self.user_id = result.get('user', {}).get('id', self.user_id)

    def _refresh(self):
        if not self.refresh_token:
            raise ApiError('Sessão expirada. Entre novamente.')
        headers = {'apikey': SUPABASE_PUBLISHABLE_KEY, 'Content-Type': 'application/json'}
        try:
            r = self.session.post(SUPABASE_URL.rstrip('/') + '/auth/v1/token',
                params={'grant_type': 'refresh_token'},
                json={'refresh_token': self.refresh_token}, headers=headers, timeout=25)
            r.raise_for_status()
            self._store_auth(r.json())
        except (requests.RequestException, ValueError, KeyError) as exc:
            self.token = None
            self.refresh_token = None
            raise ApiError('Sessão expirada. Entre novamente.') from exc

    def call(self, method, path, data=None, params=None, extra_headers=None):
        # Somente endpoints relativos do Supabase configurado: evita vazamento de tokens.
        if (not isinstance(path, str) or not path.startswith(('/auth/v1/', '/rest/v1/'))
                or path.startswith('//') or '?' in path or '#' in path
                or '..' in path or '\\' in path):
            raise ApiError('Endpoint não autorizado.')
        parsed = urlparse(SUPABASE_URL)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
            raise ApiError('Configuração segura do servidor inválida.')
        if self.token and self.expires_at and time.time() > self.expires_at - 90:
            with self._auth_lock:
                if self.token and time.time() > self.expires_at - 90:
                    self._refresh()
        headers = {'apikey': SUPABASE_PUBLISHABLE_KEY,
                   'Authorization': 'Bearer ' + self.token if self.token else '',
                   'Content-Type': 'application/json'}
        if not self.token:
            headers.pop('Authorization', None)
        if extra_headers:
            headers.update(extra_headers)
        try:
            r = self.session.request(method, SUPABASE_URL.rstrip('/') + path,
                                     json=data, params=params, headers=headers, timeout=25,
                                     allow_redirects=False)
        except requests.RequestException as exc:
            raise ApiError('Falha de conexão. Verifique sua internet e tente novamente.') from exc
        if 300 <= r.status_code < 400:
            raise ApiError('Redirecionamento inesperado do servidor. Operação cancelada.')
        if not r.ok:
            try:
                msg = r.json().get('msg') or r.json().get('message') or r.json().get('error_description')
            except ValueError:
                msg = None
            if r.status_code in (401, 403):
                raise ApiError('Acesso não autorizado. Confira seu login e as permissões.')
            if r.status_code == 429:
                raise ApiError('Muitas solicitações. Aguarde e tente novamente.')
            raise ApiError(msg or f'Erro HTTP {r.status_code}')
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    def login(self, email, password):
        result = self.call('POST', '/auth/v1/token',
                           {'email': email, 'password': password},
                           {'grant_type': 'password'})
        with self._auth_lock:
            self._store_auth(result)
        return result

    def signup(self, email, password, name):
        return self.call('POST', '/auth/v1/signup',
                         {'email': email, 'password': password,
                          'data': {'full_name': name}})

    def reset_password(self, email):
        return self.call('POST', '/auth/v1/recover', {'email': email})

    def signout(self):
        # Revoga o token no servidor quando há conectividade; limpa localmente sempre.
        try:
            if self.token:
                self.call('POST', '/auth/v1/logout')
        except Exception:
            logging.warning('Não foi possível revogar sessão remotamente.')
        finally:
            with self._auth_lock:
                self.token = None
                self.user_id = None
                self.refresh_token = None
                self.expires_at = 0

    def select(self, table, fields='*', **filters):
        params = {'select': fields, **filters}
        return self.call('GET', '/rest/v1/' + quote(table), params=params)

    def insert(self, table, row):
        return self.call('POST', '/rest/v1/' + quote(table), row,
                         extra_headers={'Prefer': 'return=representation'})

    def update(self, table, row, **filters):
        return self.call('PATCH', '/rest/v1/' + quote(table), row, filters,
                         {'Prefer': 'return=representation'})

    def upsert(self, table, rows, conflict):
        return self.call('POST', '/rest/v1/' + quote(table), rows,
                         {'on_conflict': conflict},
                         {'Prefer': 'resolution=merge-duplicates,return=representation'})


class TheChosen(App):
    def build(self):
        self.api = Supabase()
        self.staff = False
        self.students = []
        self.pending = False
        self.auth_generation = 0
        self.busy = False
        self.page_generation = 0
        self.title = 'The Chosen BJJ'
        self.root_box = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(8))
        self.root_box.add_widget(self.label('THE CHOSEN BJJ', 26, height=dp(54), color=ORANGE))
        self.content = BoxLayout(orientation='vertical', spacing=dp(8))
        self.root_box.add_widget(self.content)
        Window.bind(on_keyboard=self.on_keyboard)
        self.login_screen()
        return self.root_box

    def on_keyboard(self, window, key, scancode, codepoint, modifiers):
        # Tecla Voltar do Android: retorna ao painel sem fechar inesperadamente.
        if key in (27, 1001):
            if self.api.token:
                if self.staff:
                    self.dashboard()
                else:
                    self.student_area()
            else:
                self.login_screen()
            return True
        return False

    def label(self, value, size=16, height=dp(40), color=WHITE):
        return Label(text=str(value), font_size=dp(size), size_hint_y=None,
                     height=height, color=color, halign='left', valign='middle',
                     text_size=(dp(320), None))

    def button(self, text, action, color=ORANGE):
        b = Button(text=text, size_hint_y=None, height=dp(49),
                   background_normal='', background_color=color)
        b.bind(on_release=lambda *_: action())
        return b

    def input(self, hint, password=False, value=''):
        return TextInput(hint_text=hint, text=str(value), multiline=False,
                         password=password, size_hint_y=None, height=dp(49),
                         padding=[dp(10), dp(12)],
                         foreground_color=(0, 0, 0, 1),
                         background_color=(1, 1, 1, 1),
                         cursor_color=ORANGE)

    def page(self, heading, back=None):
        self.page_generation += 1
        self.content.clear_widgets()
        if back:
            self.content.add_widget(self.button('← Voltar', back, DARK))
        self.content.add_widget(self.label(heading, 21, dp(48)))
        scroll = ScrollView()
        box = BoxLayout(orientation='vertical', spacing=dp(9),
                        size_hint_y=None, padding=[0, dp(4), 0, dp(12)])
        box.bind(minimum_height=box.setter('height'))
        scroll.add_widget(box)
        self.content.add_widget(scroll)
        return box

    def message(self, msg):
        p = Popup(title='The Chosen BJJ', content=Label(text=str(msg),
                  text_size=(dp(275), None), halign='center'),
                  size_hint=(.88, .35))
        p.open()

    def confirm(self, title, detail, action):
        panel = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(10))
        panel.add_widget(Label(text=detail, halign='center', valign='middle',
                               text_size=(dp(270), None)))
        buttons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        dialog = Popup(title=title, content=panel, size_hint=(.92, .42))
        cancel = Button(text='Cancelar')
        accept = Button(text='Confirmar')
        cancel.bind(on_release=lambda *_: dialog.dismiss())
        def proceed(*_):
            dialog.dismiss()
            action()
        accept.bind(on_release=proceed)
        buttons.add_widget(cancel)
        buttons.add_widget(accept)
        panel.add_widget(buttons)
        dialog.open()

    def run_async(self, work, done=None):
        if self.busy:
            return self.message('Aguarde a operação anterior terminar.')
        self.busy = True
        generation = self.auth_generation
        page_generation = self.page_generation
        def finish_error(message):
            self.busy = False
            if generation == self.auth_generation and page_generation == self.page_generation:
                self.message(message)
        def finish_ok(result):
            self.busy = False
            if done and generation == self.auth_generation and page_generation == self.page_generation:
                done(result)
        def task():
            try:
                result = work()
            except Exception as exc:
                Clock.schedule_once(lambda dt, e=str(exc): finish_error(e), 0)
                return
            Clock.schedule_once(lambda dt: finish_ok(result), 0)
        threading.Thread(target=task, daemon=True).start()

    def login_screen(self):
        box = self.page('Entrar no sistema')
        email = self.input('E-mail')
        password = self.input('Senha', password=True)
        box.add_widget(email)
        box.add_widget(password)
        def enter():
            if not email.text.strip() or not password.text:
                return self.message('Informe seu e-mail e senha.')
            def work():
                self.api.login(email.text.strip(), password.text)
                if not self.api.user_id:
                    raise ApiError('Não foi possível identificar o usuário autenticado.')
                rows = self.api.select('staff_members', 'user_id', user_id='eq.' + self.api.user_id)
                return bool(rows)
            self.run_async(work, self.logged_in)
        box.add_widget(self.button('Entrar', enter))
        box.add_widget(self.button('Criar usuário', self.register_screen, DARK))
        box.add_widget(self.button('Recuperar senha', self.recover_screen, DARK))

    def register_screen(self):
        box = self.page('Criar usuário', self.login_screen)
        name, email, password = (self.input(x) for x in ('Nome completo', 'E-mail', 'Senha (mínimo 6 caracteres)'))
        password.password = True
        for w in (name, email, password): box.add_widget(w)
        def submit():
            if len(password.text) < 6 or not name.text.strip() or '@' not in email.text:
                return self.message('Informe nome, e-mail válido e senha com pelo menos 6 caracteres.')
            self.run_async(lambda: self.api.signup(email.text.strip(), password.text, name.text.strip()),
                           lambda _: (self.message('Verifique seu e-mail para confirmar o cadastro.'), self.login_screen()))
        box.add_widget(self.button('Cadastrar', submit))

    def recover_screen(self):
        box = self.page('Recuperar senha', self.login_screen)
        email = self.input('E-mail cadastrado')
        box.add_widget(email)
        box.add_widget(self.button('Enviar recuperação', lambda: self.run_async(
            lambda: self.api.reset_password(email.text.strip()),
            lambda _: self.message('Se o e-mail estiver cadastrado, você receberá instruções.'))))

    def logged_in(self, staff):
        self.staff = staff
        if staff: self.dashboard()
        else: self.student_area()

    def student_area(self):
        box = self.page('Área do aluno')
        box.add_widget(self.label('Seu cadastro está ativo, mas este usuário não tem permissão de professor.', 15, dp(100)))
        box.add_widget(self.button('Solicitar matrícula', self.enroll_screen))
        box.add_widget(self.button('Abrir sistema web', self.open_web, DARK))
        box.add_widget(self.button('Sair', self.logout, DARK))

    def dashboard(self):
        box = self.page('Painel do professor')
        for text, action in [
            ('Alunos cadastrados', self.students_screen),
            ('Cadastrar aluno', self.create_student_screen),
            ('Solicitações de matrícula', self.applications_screen),
            ('Chamada / Frequência', self.attendance_screen),
            ('Histórico de frequência', self.history_screen),
            ('Mensalidades (consulta)', self.monthly_screen),
            ('Comprovantes PIX (consulta)', self.receipts_screen),
            ('Abrir sistema web completo', self.open_web),
            ('Sair', self.logout),
        ]:
            box.add_widget(self.button(text, action))

    def open_web(self):
        try:
            webbrowser.open('https://teal-rabanadas-efb0fd.netlify.app/')
        except Exception as exc:
            self.message('Não foi possível abrir o navegador: ' + str(exc))

    def web_finance_notice(self):
        self.confirm('Operações financeiras',
            'Para proteger os registros financeiros, esta operação continua no site oficial. Abrir o navegador?',
            self.open_web)

    def logout(self):
        self.auth_generation += 1
        old_api = self.api
        self.api = Supabase()
        self.busy = False
        self.staff = False
        threading.Thread(target=old_api.signout, daemon=True).start()
        self.login_screen()

    def students_screen(self):
        box = self.page('Alunos cadastrados', self.dashboard)
        box.add_widget(self.label('Carregando...'))
        def done(rows):
            self.students = rows
            box.clear_widgets()
            box.add_widget(self.label(f'{len(rows)} alunos'))
            search = self.input('Pesquisar por nome')
            box.add_widget(search)
            results = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6))
            results.bind(minimum_height=results.setter('height'))
            box.add_widget(results)
            def render(*_):
                results.clear_widgets()
                for s in rows:
                    if search.text.lower() not in s['name'].lower(): continue
                    results.add_widget(self.button(s['name'] + ' • ' + s.get('group_name', ''),
                                                   lambda item=s: self.edit_student_screen(item), DARK))
            search.bind(text=render)
            render()
        self.run_async(lambda: self.api.select('students',
            'id,name,cpf,birth,group_name,monthly,emergency,guardian',
            order='name.asc', limit='500'), done)

    def edit_student_screen(self, student):
        box = self.page('Editar aluno', self.students_screen)
        fields = {}
        for key, hint in [('name', 'Nome completo'), ('cpf', 'CPF'),
                          ('birth', 'Nascimento AAAA-MM-DD'),
                          ('group_name', 'Turma: Kids ou Adultos'),
                          ('emergency', 'Contato de emergência')]:
            fields[key] = self.input(hint, value=student.get(key) or '')
            box.add_widget(fields[key])
        def save():
            data = {k: w.text.strip() for k, w in fields.items()}
            if not data['name']:
                return self.message('Informe o nome do aluno.')
            try:
                datetime.date.fromisoformat(data['birth'])
            except ValueError:
                return self.message('Data de nascimento inválida. Use AAAA-MM-DD.')
            if data['group_name'] not in ('Kids', 'Adultos'):
                return self.message('Turma deve ser Kids ou Adultos.')
            if data['cpf'] and not valid_cpf(data['cpf']):
                return self.message('CPF inválido.')
            data['cpf'] = re.sub(r'\D', '', data['cpf'])
            self.run_async(lambda: self.api.update('students', data, id='eq.' + str(student['id'])),
                           lambda result: (self.message('Dados atualizados.' if result else 'Nenhuma alteração confirmada. Confira as permissões.'), self.students_screen()))
        box.add_widget(self.button('Salvar alterações', save))

    def create_student_screen(self):
        box = self.page('Cadastrar aluno', self.dashboard)
        fields = {}
        for key, hint in [('name','Nome completo'),('cpf','CPF (11 dígitos)'),
                          ('birth','Nascimento AAAA-MM-DD'),('group_name','Turma: Kids ou Adultos'),
                          ('emergency','Contato de emergência'),
                          ('guardian','Nome do responsável (se menor)'),
                          ('health_note','Observações de saúde (opcional)')]:
            fields[key] = self.input(hint)
            box.add_widget(fields[key])
        monthly = CheckBox(size_hint_x=None, width=dp(50))
        health_issue = CheckBox(size_hint_x=None, width=dp(50))
        for title, check in [('Possui mensalidade', monthly), ('Possui condição de saúde a informar', health_issue)]:
            line = BoxLayout(size_hint_y=None, height=dp(48))
            line.add_widget(check)
            line.add_widget(self.label(title, 14))
            box.add_widget(line)
        box.add_widget(self.label('O professor deve confirmar os termos e autorizações antes do cadastro.', 14, dp(90)))
        def save():
            data = {k: v.text.strip() for k, v in fields.items()}
            if not data['name'] or not data['cpf'] or not data['birth'] or not data['emergency'] or data['group_name'] not in ('Kids','Adultos'):
                return self.message('Preencha os campos obrigatórios e informe Kids ou Adultos.')
            if not valid_cpf(data['cpf']):
                return self.message('CPF inválido. Confira os 11 dígitos.')
            try:
                born = datetime.date.fromisoformat(data['birth'])
            except ValueError:
                return self.message('Data de nascimento inválida. Use AAAA-MM-DD.')
            today = datetime.date.today()
            if born > today:
                return self.message('Nascimento não pode ser uma data futura.')
            if (today.year - born.year - ((today.month, today.day) < (born.month, born.day))) < 18 and not data['guardian']:
                return self.message('Informe o responsável legal pelo aluno menor de idade.')
            payload = dict(data)
            payload['cpf'] = re.sub(r'\D', '', data['cpf'])
            payload['monthly'] = monthly.active
            payload['health_issue'] = health_issue.active
            if not payload['health_note']: payload['health_note'] = None
            if not payload['guardian']: payload['guardian'] = None
            # Observações de saúde são dados sensíveis: não enviar sem política de acesso validada.
            if payload.get('health_note') or payload.get('health_issue'):
                return self.message('Dados de saúde devem ser cadastrados apenas no sistema web autorizado.')
            self.confirm('Termos e autorizações',
                'Confirme somente após verificar os termos vigentes e as autorizações exigidas, especialmente para menores. Prosseguir com o cadastro?',
                lambda: self.run_async(lambda: self.api.insert('students', payload),
                    lambda rows: (self.message('Aluno cadastrado.' if rows else 'Cadastro não confirmado. Verifique permissões e registros.'), self.dashboard())))
        box.add_widget(self.button('Salvar cadastro', save))

    def enroll_screen(self):
        box = self.page('Solicitação de matrícula', self.student_area)
        fields = {}
        for key, hint in [('name','Nome completo'),('cpf','CPF'),('birth','Nascimento AAAA-MM-DD'),
                          ('guardian','Responsável legal (se menor)'),('emergency','Contato de emergência'),
                          ('address','Endereço'),('email','E-mail')]:
            fields[key] = self.input(hint)
            box.add_widget(fields[key])
        responsibility = CheckBox(size_hint_x=None, width=dp(50))
        image_consent = CheckBox(size_hint_x=None, width=dp(50))
        box.add_widget(self.label('Confira o termo integral vigente no site. A autorização de imagem é opcional.', 14, dp(110)))
        box.add_widget(self.button('Ler termos no site oficial', self.open_web, DARK))
        for title, check in [('Li e aceito o termo de responsabilidade', responsibility),
                             ('Autorizo o uso de imagem (opcional)', image_consent)]:
            line = BoxLayout(size_hint_y=None, height=dp(60))
            line.add_widget(check)
            line.add_widget(self.label(title, 14, dp(60)))
            box.add_widget(line)
        def submit():
            data = {k: v.text.strip() for k, v in fields.items()}
            if not all(data[k] for k in ('name','cpf','birth','emergency','address','email')):
                return self.message('Preencha todos os campos obrigatórios.')
            if not valid_cpf(data['cpf']):
                return self.message('CPF inválido.')
            try:
                born = datetime.date.fromisoformat(data['birth'])
            except ValueError:
                return self.message('Data de nascimento inválida.')
            today = datetime.date.today()
            if born > today:
                return self.message('Data de nascimento futura.')
            if (today.year - born.year - ((today.month, today.day) < (born.month, born.day))) < 18 and not data['guardian']:
                return self.message('Informe o responsável legal.')
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', data['email']):
                return self.message('Informe um e-mail válido.')
            if not responsibility.active:
                return self.message('É necessário ler e aceitar o termo de responsabilidade.')
            data['cpf'] = re.sub(r'\D', '', data['cpf'])
            data['responsibility_consent'] = True
            data['image_consent'] = image_consent.active
            self.confirm('Enviar solicitação', 'Confirmar envio da matrícula para análise?',
                lambda: self.run_async(lambda: self.api.insert('applications', data),
                    lambda rows: (self.message('Matrícula enviada para análise.' if rows else 'Envio não confirmado. Verifique a solicitação no site.'), self.student_area())))
        box.add_widget(self.button('Enviar matrícula', submit))

    def applications_screen(self):
        box = self.page('Solicitações de matrícula', self.dashboard)
        def done(rows):
            box.clear_widgets()
            box.add_widget(self.label(f'{len(rows)} solicitações'))
            for r in rows:
                box.add_widget(self.label(f"{r['name']} • {r['status']}", 15, dp(60)))
                if r.get('status') == 'Pendente':
                    for turma in ('Kids', 'Adultos'):
                        box.add_widget(self.button('Aprovar em ' + turma, lambda row=r, group=turma: self.approve_application(row['id'], group), DARK))
        self.run_async(lambda: self.api.select('applications',
            'id,name,cpf,email,status,created_at', order='created_at.desc', limit='200'), done)

    def approve_application(self, application_id, group):
        if not application_id or group not in ('Kids', 'Adultos'):
            return self.message('Solicitação ou turma inválida.')
        self.confirm('Confirmar aprovação',
            'Aprovar esta matrícula na turma ' + group + '?',
            lambda: self.run_async(lambda: self.api.call('POST', '/rest/v1/rpc/approve_application',
                {'application_id': application_id, 'chosen_group': group}),
                lambda result: (self.message('Solicitação processada. Confira o status atualizado.'), self.applications_screen())))

    def attendance_screen(self):
        box = self.page('Chamada / Frequência', self.dashboard)
        date = self.input('Data AAAA-MM-DD', value=datetime.date.today().isoformat())
        group = self.input('Turma: Kids ou Adultos', value='Adultos')
        box.add_widget(date); box.add_widget(group)
        listing = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6))
        listing.bind(minimum_height=listing.setter('height'))
        box.add_widget(listing)
        selected = {}
        loaded_context = {'date': None, 'group': None}
        def invalidate(*_):
            selected.clear()
            listing.clear_widgets()
            loaded_context.update(date=None, group=None)
        date.bind(text=invalidate)
        group.bind(text=invalidate)
        def load():
            if group.text.strip() not in ('Kids', 'Adultos'):
                return self.message('Selecione Kids ou Adultos.')
            try:
                datetime.date.fromisoformat(date.text.strip())
            except ValueError:
                return self.message('Data inválida. Use AAAA-MM-DD.')
            requested_date = date.text.strip()
            requested_group = group.text.strip()
            def done(rows):
                if date.text.strip() != requested_date or group.text.strip() != requested_group:
                    return self.message('Data ou turma alterada durante a consulta. Carregue novamente.')
                listing.clear_widgets(); selected.clear()
                loaded_context.update(date=requested_date, group=requested_group)
                for s in rows:
                    if s['group_name'] != group.text.strip(): continue
                    line = BoxLayout(size_hint_y=None, height=dp(45))
                    check = CheckBox(size_hint_x=None, width=dp(55))
                    selected[s['id']] = check
                    line.add_widget(check)
                    line.add_widget(self.label(s['name'], 15))
                    listing.add_widget(line)
            self.run_async(lambda: self.api.select('students', 'id,name,group_name', order='name.asc', limit='500'), done)
        def save():
            if not selected: return self.message('Carregue os alunos da turma primeiro.')
            if loaded_context['date'] != date.text.strip() or loaded_context['group'] != group.text.strip():
                return self.message('A turma ou a data mudou. Recarregue a lista antes de salvar.')
            if group.text.strip() not in ('Kids', 'Adultos'):
                return self.message('Turma inválida. Carregue a turma novamente.')
            try:
                datetime.date.fromisoformat(date.text.strip())
            except ValueError:
                return self.message('Data inválida. Use AAAA-MM-DD.')
            rows = [{'student_id': sid, 'class_date': date.text.strip(),
                     'present': check.active, 'recorded_by': self.api.user_id}
                    for sid, check in selected.items()]
            self.confirm('Confirmar chamada',
                'Registrar presença e ausência de ' + str(len(rows)) + ' alunos? Esta ação altera os registros no banco.',
                lambda: self.run_async(lambda: self.api.upsert('attendance', rows, 'student_id,class_date'),
                           lambda result: self.message('Chamada salva no Supabase.' if result else 'Não foi possível confirmar a chamada. Verifique no sistema web.')))
        box.add_widget(self.button('Carregar turma', load))
        box.add_widget(self.button('Salvar chamada', save))

    def history_screen(self):
        box = self.page('Histórico de frequência', self.dashboard)
        def done(rows):
            box.clear_widgets()
            for r in rows:
                s = r.get('students') or {}
                box.add_widget(self.label(f"{r['class_date']} • {s.get('name','Aluno')} • {'Presente' if r['present'] else 'Ausente'}", 14, dp(50)))
        self.run_async(lambda: self.api.select('attendance',
            'class_date,present,students(name,group_name)', order='class_date.desc', limit='300'), done)

    def monthly_screen(self):
        box = self.page('Mensalidades - somente consulta', self.dashboard)
        box.add_widget(self.label('Buscando mensalidades...', 15, dp(60)))
        def done(rows):
            box.clear_widgets()
            box.add_widget(self.label(f'{len(rows)} registros encontrados', 15, dp(45)))
            for r in rows:
                box.add_widget(self.label(
                    f"{r.get('reference_month','')} • {r.get('status','')} • R$ {r.get('amount','')}",
                    14, dp(54)))
            box.add_widget(self.button('Gerenciar no sistema web', self.open_web, DARK))
        # O esquema pode variar entre versões do banco; não faz alterações financeiras.
        self.run_async(lambda: self.api.select('monthly_payments', '*',
                       order='reference_month.desc', limit='100'), done)

    def receipts_screen(self):
        box = self.page('Comprovantes PIX', self.dashboard)
        def done(rows):
            box.clear_widgets()
            for r in rows:
                text = f"{r['student_name']} • {r['status']} • R$ {r['amount']}"
                box.add_widget(self.button(text, lambda item=r: self.receipt_detail(item), DARK))
        self.run_async(lambda: self.api.select('payment_submissions',
            'id,student_name,student_cpf,payer_name,reference_month,amount,paid_at,receipt_path,status',
            order='created_at.desc', limit='200'), done)

    def receipt_detail(self, r):
        box = self.page('Comprovante PIX', self.receipts_screen)
        for field in ('student_name','student_cpf','payer_name','reference_month','amount','paid_at','status'):
            box.add_widget(self.label(f'{field}: {r.get(field)}', 15, dp(48)))
        box.add_widget(self.label('Aprovação de comprovantes ainda deve ser feita pelo sistema web, com conferência do documento.', 14, dp(100)))
        box.add_widget(self.button('Conferir no sistema web', self.open_web))

if __name__ == '__main__':
    TheChosen().run()
