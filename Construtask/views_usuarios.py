"""
Views para gerenciamento de usuarios e obras da empresa.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View

from .models import Empresa, Obra, UsuarioEmpresa
from .permissions import get_empresa_do_usuario, is_admin_empresa
from .domain import gerar_numero_documento

User = get_user_model()


class EmpresaAdminForm(forms.Form):
    """Formulario para criar obra."""
    codigo = forms.CharField(max_length=30, required=False, disabled=True)
    nome = forms.CharField(max_length=150, required=True)
    cliente = forms.CharField(max_length=150, required=False)
    responsavel = forms.CharField(max_length=150, required=False)
    status = forms.ChoiceField(choices=Obra._meta.get_field("status").choices, initial="PLANEJADA")
    data_inicio = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    data_fim = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    descricao = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


class UsuarioEmpresaListView(View):
    """
    Gerencia usuarios e obras da empresa do admin logado.
    Uma unica tela para ambas as funcoes.
    """
    template_name = "app/empresa_admin.html"
    
    def get(self, request):
        if not is_admin_empresa(request.user) and not request.user.is_superuser:
            messages.error(request, "Voce nao tem permissao para acessar esta pagina.")
            return redirect("home")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa and not request.user.is_superuser:
            messages.error(request, "Voce nao esta vinculado a nenhuma empresa.")
            return redirect("home")
        
        if request.user.is_superuser:
            empresa_id = request.GET.get("empresa")
            if empresa_id:
                empresa = Empresa.objects.filter(pk=empresa_id).first()
            if not empresa:
                empresa = Empresa.objects.first()
        
        if not empresa:
            messages.error(request, "Nenhuma empresa encontrada.")
            return redirect("home")
        
        # Usuarios da empresa
        usuarios_empresa = (
            UsuarioEmpresa.objects
            .filter(empresa=empresa)
            .select_related("usuario")
            .prefetch_related("obras_permitidas")
            .order_by("usuario__username")
        )
        
        # Obras da empresa
        obras_da_empresa = (
            Obra.objects
            .filter(empresa=empresa)
            .order_by("codigo")
        )
        
        # Form para nova obra
        obra_form = EmpresaAdminForm()
        
        return render(request, self.template_name, {
            "empresa": empresa,
            "usuarios_empresa": usuarios_empresa,
            "obras_da_empresa": obras_da_empresa,
            "obra_form": obra_form,
            "empresas": Empresa.objects.filter(ativo=True).order_by("nome") if request.user.is_superuser else None,
        })
    
    def post(self, request):
        """Processa acoes de usuarios ou obras."""
        if not is_admin_empresa(request.user) and not request.user.is_superuser:
            messages.error(request, "Voce nao tem permissao.")
            return redirect("home")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa and not request.user.is_superuser:
            return redirect("home")
        
        if request.user.is_superuser:
            empresa_id = request.POST.get("empresa_id")
            if empresa_id:
                empresa = Empresa.objects.filter(pk=empresa_id).first()
        
        if not empresa:
            messages.error(request, "Empresa nao encontrada.")
            return redirect("home")
        
        # Verificar qual acao
        acao = request.POST.get("acao")
        
        if acao == "atualizar_obras_usuario":
            return self._atualizar_obras_usuario(request, empresa)
        elif acao == "criar_usuario":
            return self._criar_usuario(request, empresa)
        elif acao == "criar_obra":
            return self._criar_obra(request, empresa)
        
        return redirect("empresa_admin")
    
    def _atualizar_obras_usuario(self, request, empresa):
        """Atualizar obras permitidas de um usuario."""
        usuario_empresa_id = request.POST.get("usuario_empresa_id")
        obras_selecionadas = request.POST.getlist("obras")
        
        try:
            usuario_empresa = UsuarioEmpresa.objects.get(pk=usuario_empresa_id)
            
            admin_empresa = get_empresa_do_usuario(request.user)
            if not request.user.is_superuser and admin_empresa != usuario_empresa.empresa:
                messages.error(request, "Voce so pode gerenciar usuarios da sua empresa.")
                return redirect("empresa_admin")
            
            if not usuario_empresa.is_admin_empresa:
                obras = Obra.objects.filter(pk__in=obras_selecionadas, empresa=usuario_empresa.empresa)
                usuario_empresa.obras_permitidas.set(obras)
                messages.success(request, f"Obras atualizadas para {usuario_empresa.usuario.username}.")
            else:
                messages.info(request, "Admin da empresa ja tem acesso a todas as obras.")
            
        except UsuarioEmpresa.DoesNotExist:
            messages.error(request, "Usuario empresa nao encontrado.")
        
        return redirect("empresa_admin")
    
    def _criar_usuario(self, request, empresa):
        """Criar novo usuario."""
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        obras_selecionadas = request.POST.getlist("obras_usuario")
        
        if not username or not password:
            messages.error(request, "Username e senha sao obrigatorios.")
            return redirect("empresa_admin")
        
        if User.objects.filter(username=username).exists():
            messages.error(request, "Ja existe um usuario com este username.")
            return redirect("empresa_admin")
        
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=True,
            is_active=True,
        )
        
        usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=user,
            empresa=empresa,
            is_admin_empresa=False,
        )
        
        if obras_selecionadas:
            obras = Obra.objects.filter(pk__in=obras_selecionadas, empresa=empresa)
            usuario_empresa.obras_permitidas.set(obras)
        
        messages.success(request, f"Usuario {username} criado com sucesso!")
        return redirect("empresa_admin")
    
    def _criar_obra(self, request, empresa):
        """Criar nova obra para a empresa."""
        form = EmpresaAdminForm(request.POST)
        
        if form.is_valid():
            # Gerar codigo unico (loop para garantir unicidade)
            codigo = None
            for tentativa in range(100):
                temp_codigo = gerar_numero_documento(Obra, "OBRA-", "codigo")
                if not Obra.objects.filter(codigo=temp_codigo).exists():
                    codigo = temp_codigo
                    break
            
            if not codigo:
                messages.error(request, "Nao foi possivel gerar um codigo unico para a obra.")
                return redirect("empresa_admin")
            
            try:
                obra = Obra.objects.create(
                    empresa=empresa,
                    codigo=codigo,
                    nome=form.cleaned_data["nome"],
                    cliente=form.cleaned_data.get("cliente", ""),
                    responsavel=form.cleaned_data.get("responsavel", ""),
                    status=form.cleaned_data["status"],
                    data_inicio=form.cleaned_data.get("data_inicio"),
                    data_fim=form.cleaned_data.get("data_fim"),
                    descricao=form.cleaned_data.get("descricao", ""),
                )
                
                messages.success(request, f"Obra {obra.codigo} - {obra.nome} criada com sucesso!")
            except Exception as e:
                messages.error(request, f"Erro ao criar obra: {str(e)}")
        else:
            messages.error(request, "Erro ao criar obra. Verifique os dados.")
        
        return redirect("empresa_admin")


class UsuarioEmpresaCreateView(View):
    """
    Criar um novo usuario e vincula-lo a empresa do admin.
    """
    template_name = "app/usuario_empresa_form.html"
    
    def get(self, request):
        if not is_admin_empresa(request.user) and not request.user.is_superuser:
            messages.error(request, "Voce nao tem permissao para acessar esta pagina.")
            return redirect("home")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa and not request.user.is_superuser:
            messages.error(request, "Voce nao esta vinculado a nenhuma empresa.")
            return redirect("home")
        
        if request.user.is_superuser:
            empresa_id = request.GET.get("empresa")
            if empresa_id:
                empresa = Empresa.objects.filter(pk=empresa_id).first()
        
        return render(request, self.template_name, {
            "empresa": empresa,
        })
    
    def post(self, request):
        if not is_admin_empresa(request.user) and not request.user.is_superuser:
            messages.error(request, "Voce nao tem permissao.")
            return redirect("home")
        
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        obras_selecionadas = request.POST.getlist("obras")
        empresa_id = request.POST.get("empresa_id")
        
        if not username or not password:
            messages.error(request, "Username e senha sao obrigatorios.")
            return redirect("usuario_empresa_create")
        
        empresa = get_empresa_do_usuario(request.user)
        if not empresa and not request.user.is_superuser:
            return redirect("home")
        
        if request.user.is_superuser and empresa_id:
            empresa = Empresa.objects.filter(pk=empresa_id).first()
        
        if not empresa:
            messages.error(request, "Empresa nao encontrada.")
            return redirect("home")
        
        # Verificar se username ja existe
        if User.objects.filter(username=username).exists():
            messages.error(request, "Ja existe um usuario com este username.")
            return redirect("usuario_empresa_create")
        
        # Criar usuario
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=True,
            is_active=True,
        )
        
        # Criar vinculo com empresa
        usuario_empresa = UsuarioEmpresa.objects.create(
            usuario=user,
            empresa=empresa,
            is_admin_empresa=False,
        )
        
        # Liberar obras
        if obras_selecionadas:
            obras = Obra.objects.filter(pk__in=obras_selecionadas, empresa=empresa)
            usuario_empresa.obras_permitidas.set(obras)
        
        messages.success(request, f"Usuario {username} criado com sucesso!")
        return redirect("usuario_empresa_list")
