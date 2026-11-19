# Déploiement sur SLES / VirtualBox

## 1. Machine virtuelle

| Ressource | Dimensionnement |
|---|---|
| vCPU | 4 à 6 |
| RAM | 12 à 16 Go |
| Disque SSD | 100 à 150 Go (dynamique) |
| OS | SUSE Linux Enterprise Server 15 SP6 ou plus récent |
| Réseau | Carte en accès par pont (bridged) pour être joignable sur le LAN, ou NAT avec redirection des ports 80/443 |

Dans VirtualBox : *Nouvelle* → type Linux / openSUSE (64-bit) → RAM et CPU ci-dessus → disque VDI → *Réseau* : « Accès par pont ». Installez SLES avec le rôle *Text Mode* et enregistrez le système (SUSEConnect).

## 2. Préparation du système

```bash
sudo zypper install -y git make
git clone https://github.com/plyts/datahub /opt/datahub && cd /opt/datahub
sudo ./scripts/bootstrap-sles.sh $USER     # Docker Engine + Compose, rotation des logs, pare-feu 80/443, sysctl
# se déconnecter / reconnecter pour appartenir au groupe docker
docker compose version                      # v2.17 ou plus récent
```

## 3. Nom d'hôte

Les utilisateurs accèdent au Data Hub par **un nom** (`DATAHUB_HOST`), pas par une IP. L'OIDC exige que l'URL soit identique pour tous :

- avec un DNS interne : enregistrement A `datahub.internal` → IP de la VM ;
- sans DNS : ajoutez `IP_DE_LA_VM datahub.local` dans `/etc/hosts` (Linux/macOS) ou `C:\Windows\System32\drivers\etc\hosts` (Windows) sur chaque poste.

Adaptez `DATAHUB_HOST`, `DATAHUB_PUBLIC_URL` et `DATAHUB_SITE_ADDRESS` dans `envs/<env>.env`.

## 4. HTTP ou HTTPS

- **HTTP** (`http://datahub.local`) : acceptable sur un LAN privé. Keycloak (`sslRequired=external`) accepte HTTP pour les adresses privées (10/8, 172.16/12, 192.168/16).
- **HTTPS** (recommandé, obligatoire en prod) : `DATAHUB_PUBLIC_URL=https://...`, `DATAHUB_SITE_ADDRESS=https://...`, `COOKIE_SECURE=true`.
  - nom en `.local` / `.internal` : Caddy génère un certificat avec **sa propre autorité de certification interne**. Exportez la racine (`docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt .`) et installez-la sur les postes ;
  - certificat d'entreprise : montez-le dans Caddy et ajoutez `tls /chemin/cert.pem /chemin/key.pem` au site du `Caddyfile`.

## 5. Premier démarrage

```bash
make secrets                  # crée .env (droits 600) — sauvegardez-le dans un coffre
make up ENV=prod              # ou dev / staging
make smoke ENV=prod
```

Le premier démarrage construit les deux images applicatives (~5 min), initialise PostgreSQL (une base et un rôle par service), importe le realm Keycloak et applique les migrations du Data Hub.

Connexion : `https://<hôte>/` avec `DATAHUB_ADMIN_USERNAME` / `DATAHUB_ADMIN_PASSWORD` (`.env`). **Changez ce mot de passe** via *11 · IAM → Gérer mon compte*. Créez ensuite les comptes de l'équipe dans Keycloak et rattachez-les aux groupes `/admins`, `/data-engineers`, `/analysts` ou `/viewers`.

## 6. Initialisation des données

Lancez un backfill des derniers jours (*07 · Backfill & Recovery*). À partir de là, le schedule quotidien prend le relais à 05:30. Le sensor régénère ensuite automatiquement le catalogue dbt.

## 7. Vérifications

```bash
make ps                       # tous les services « healthy »
make smoke ENV=prod
make logs S=dagster-code      # logs d'un service
```

Dans Grafana (*Data Hub — Infrastructure*), toutes les cibles doivent être UP.
