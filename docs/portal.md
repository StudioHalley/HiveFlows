# Tunnel aka “HiveFlows Portal”

“HiveFlows Portal” est le “branding name” du projet.


C'est une excellente idée, et c'est exactement le pattern qu'utilisent des outils comme Cloudflare Tunnel, ngrok, ou Tailscale. Le concept clé c'est le **reverse tunnel** avec connexion initiée par le client.

Voici comment ça fonctionne conceptuellement :Le pattern fondamental ici c'est le **reverse tunnel via WebSocket persistant**. La gateway sur l'ordi de l'usager *initie* la connexion vers ton serveur — ce qui contourne les NAT, pare-feux, et IP dynamiques. Ton serveur ne se connecte jamais directement à l'ordi.

---

## Les composants clés à implémenter

**1\. La gateway app (client, installée chez l'usager)** 

C'est le cœur du système. Elle doit :

*   Ouvrir une connexion WebSocket persistante vers ton VPS au démarrage.
*   
*   S'authentifier avec un token unique par usager. Utilisé les tokens de “/dashboard/workspaces/{workspace_slug}/manage” ?
Question:  Faut-il avoir un type de token exclusive au tunnel ? (créé une permission “portal” pour les identifier ?) Une user peut avoir plusieurs workspaces... il peut aussi configurer plusieurs tunnel.
Un tunnel devrait être identifier avec un “Device”. Par exemple, un device “NAS Server”, “Laptop Bureau”, “Ordinateur Maison”.

  
*   Écouter les requêtes proxy qui arrivent via ce WebSocket
*   Faire la requête vers le service local (`localhost:8080`) et renvoyer la réponse

**2\. Le tunnel manager (VPS)**

C’est une app qu’on install via le BeeKeeper. (donc un docker.)
  Question : Est-ce que ca alourdit ? Est-ce plus simple de tout intégré dans le core ?

Il gère le registre des clients connectés :

*   `Map<deviceId, WebSocketConnection>`
*   Quand une requête arrive pour l'usager X, il retrouve sa connexion et y injecte la requête

**3\. Le proxy interne (VPS)**

L'app web fait une requête HTTP normale genre `GET /tunnel/{workspace_slug}/{device_slug}/api/data` — le proxy traduit ça en message WebSocket vers le bon client.

---

## Stack recommandée

Pour la gateway app, **deux bonnes options** :

*   **Electron ou Tauri** — si tu veux une vraie app desktop avec icône dans le tray, installation propre
*   **Simple exécutable Node.js / Go / Python** — plus léger, lancé en service Windows/Mac/Linux

Pour le serveur, n'importe quoi qui gère bien les WebSockets : **Node.js + ws**, **Go + gorilla/websocket**, ou **Python + websockets**.

---

## Ce qu'il faut réfléchir côté sécurité

*   **Authentification bidirectionnelle** : token signé (JWT) que la gateway présente à la connexion
*   **Autorisation par route** : l'usager déclare quels ports/services il accepte d'exposer (pas d'accès libre à tout son localhost)
*   **TLS obligatoire** : le WebSocket doit tourner en `wss://` (ce que tu as déjà avec Cloudflare ou un certificat direct)
*   **Timeout et rate limiting** : pour éviter qu'une app web abuse de la connexion d'un usager

Tu veux qu'on creuse l'implémentation d'un composant en particulier ? La gateway client, le tunnel manager côté serveur, ou le protocole de messagerie entre les deux ?
