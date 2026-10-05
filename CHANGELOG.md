# Notas de los parches

Cambios de Tracker360, del más nuevo al más viejo. Cada entrada corresponde a un push a `main`.

## Sin versión todavía (2026-10-05)

### Agregado (HTTPS en la instalación con Docker)
- `install.sh` configura HTTPS: levanta un contenedor de Caddy (`tracker360_caddy`, perfil `https` del compose)
  delante de la API. Con dominio saca el certificado solo; sin dominio usa la IP del servidor con la CA local de
  Caddy y deja el certificado raíz en `caddy/ca-local.crt`. Si el 443 ya está en uso, queda en el 8443. Así se
  puede ingresar desde otras PCs, colectoras y celulares también con Docker (antes, sin dominio, quedaba por
  http y la sesión no se guardaba). Al terminar muestra un aviso con la dirección y qué hacer con el
  certificado. Las instalaciones existentes lo reciben al volver a correr el instalador. Para no usarlo:
  `TRACKER360_HTTPS=no ./install.sh`.

### Cambiado (páginas legales)
- Política de privacidad revisada según la Ley 25.326 de Protección de los Datos Personales: quién es el
  responsable de los datos (la organización) y el papel de JZ Tech Solutions como prestador de servicios de
  tratamiento (art. 25), base del tratamiento, datos sensibles, transferencias internacionales (Google,
  webhooks), plazos para ejercer los derechos (acceso en 10 días corridos, rectificación o supresión en 5 días
  hábiles), la leyenda obligatoria de la Agencia de Acceso a la Información Pública y lo que le corresponde a la
  organización (informar e inscribir sus bases de datos).
- Términos y condiciones: ley aplicable y jurisdicción argentinas, propiedad intelectual (Ley 11.723), aclaración
  de que los documentos de Tracker360 no reemplazan a los comprobantes fiscales de ARCA, y la limitación de
  responsabilidad ajustada al Código Civil y Comercial (art. 1743) y a la Ley 24.240 de Defensa del Consumidor.

### Corregido
- Alta manual de pedidos: con un CUIT que no corresponde a un cliente activo da el error "Cliente no encontrado"
  (antes creaba el pedido sin cliente y sin avisar). Un CUIT de solo proveedor o de un cliente dado de baja
  tampoco sirve. El rechazo no gasta un número de pedido, y el CUIT se reconoce aunque se pegue con espacios.

## 1.0.0 — 2026-10-05

Primer release estable (tag `v1.0.0`, publicado en GitHub Releases). No cambia código: marca como versión
1.0.0 todo lo que está abajo. Verificado con la suite de tests de la Fase 1 (485 en verde) y el instalador
nativo en Debian 12/13 y Ubuntu 24.04. Desde acá, cada release se numera (1.x).

## 2026-10-05

### Agregado (instalación sin Docker)
- Instalador para servidores sin Docker: `sudo ./install-native.sh` (Debian 12/13, Ubuntu 24.04). Deja Tracker360
  como servicio del sistema (`tracker360`, usuario propio sin login, código de solo lectura), con su base y rol
  en el PostgreSQL del servidor y Caddy con HTTPS delante: con `TRACKER360_DOMAIN` saca el certificado solo; sin
  dominio usa la IP del servidor con la CA local de Caddy. Así se puede ingresar desde otras PCs (antes, sin
  dominio, la instalación quedaba por http y la sesión no se guardaba). Instala las dependencias sin compilar,
  verificando los hashes, y se puede volver a correr sin pisar secretos ni configuración agregada a mano.
- Actualizador `sudo tracker360-actualizar` (`--buscar`, `--volver`): arma la versión nueva aparte, respalda la
  base, cambia y verifica que responda; si no responde, vuelve solo a la versión anterior y, si el esquema cambió,
  restaura la base como estaba.
- `.gitattributes`: los scripts de Linux siempre con finales de línea LF.

### Cambiado (dependencias)
- Versiones de las dependencias alineadas con JZ Middle ML-Tracker y JZPass: FastAPI 0.141.1 (Starlette 1.7),
  uvicorn 0.54, pydantic 2.13.5, asyncpg 0.31, Pillow 12.3 y python-multipart 0.0.32. Las anteriores no tenían
  paquetes binarios para Python 3.13 (el de Debian 13), así que no se podían instalar sin compilar fuera de
  Docker. Sin cambios para el usuario; la imagen de Docker sigue en Python 3.11.

## 2026-10-03

### Arreglado (ubicaciones)
- Crear una ubicación sin descripción (desde Depósitos, dejando el campo vacío) daba "Error interno del
  servidor": la descripción es opcional y el panel la manda vacía. Encontrado al configurar una instalación
  nueva en la VM de pruebas.

### Arreglado (instalador y actualización)
- Volver a correr `install.sh` para actualizar no actualizaba: reconstruía la misma versión que ya estaba
  instalada. Ahora trae la última versión publicada (`git pull --ff-only`; con cambios locales o sin conexión
  se detiene sin tocar nada), guarda antes una copia de la base en `backups/` y recién ahí reconstruye.
  `TRACKER360_NO_UPDATE=1` reconstruye sin actualizar.
- Corrido desde la carpeta donde se instaló con `curl ... | bash`, intentaba clonar otra vez y fallaba
  porque `tracker360/` ya existía: ahora actualiza esa instalación.
- `install.sh` no tenía permiso de ejecución en el repositorio: `./install.sh` (como dice el README) daba
  "Permiso denegado".
- Con `curl ... | bash`, la copia de la base (`docker compose exec`) leía la entrada estándar y se comía el
  resto del instalador, que terminaba sin reconstruir; ahora la copia no lee la entrada.
- Instalaciones anteriores: la primera vez hay que actualizar con el `curl ... | bash` o con `git pull` a mano,
  porque su instalador es el viejo.

### Agregado (devoluciones)
- Pantalla de alta de devoluciones (Compras y Recepción → Devoluciones → "+ Nueva devolución"; antes el
  formulario no funcionaba). Se busca el pedido por número, venta del canal, cliente o comprador (solo los
  completos o despachados, también los de Mercado Libre), se elige la sucursal y el sector donde entra la
  mercadería y se carga cuánto vuelve de cada artículo y en qué estado: operativo (vuelve a la venta) o
  cuarentena (apartado para revisar). Muestra lo preparado y lo ya devuelto y no deja pasarse; al guardar
  aparece en el historial.

### Arreglado (devoluciones)
- El alta de devoluciones no controlaba nada del pedido: se podía devolver un pedido sin preparar, un artículo
  que no estaba en el pedido o más unidades de las preparadas (también devolviendo el mismo pedido varias
  veces). Cada devolución suma stock, así que eso inflaba el inventario. Ahora solo se devuelven pedidos
  completos o despachados, solo sus artículos y como mucho lo preparado menos lo ya devuelto (dos devoluciones
  simultáneas del mismo pedido no pueden pasarse del total).
- El cliente de la devolución se toma del pedido. Antes era obligatorio y no se controlaba: se podía cargar un
  cliente que no era el del pedido, y los pedidos de canales de venta (Mercado Libre, sin cliente cargado) no se
  podían devolver. API: en `POST /api/admin/returns` el `customer_id` pasa a ser opcional; si viene, tiene que
  ser el del pedido. Ruta nueva `GET /api/admin/returns/orders?q=` (pedidos que se pueden devolver, por número,
  venta, cliente o comprador) y el detalle del pedido suma `returned` (lo ya devuelto por SKU).

### Agregado
- Instructivo completo (`/instructivo.html`): guía de todo el sistema, de los primeros pasos a Mercado Libre paso
  a paso, con índice, problemas frecuentes, glosario, modo oscuro y versión para imprimir.

- Sección Soporte en el panel (para administradores y supervisores): contacto de JZ Tech Solutions, qué incluir
  al reportar un problema y acceso al instructivo, al modo ayuda y a las condiciones de uso. Acceso al
  instructivo en el menú lateral y enlaces a instructivo, privacidad, términos y soporte en la pantalla de ingreso.

### Arreglado
- Artículos: los botones Anterior/Siguiente y el orden por columna no hacían nada (las funciones no
  existían). Ahora paginan y ordenan por SKU, descripción o stock. `GET /api/admin/items` con `page=0` daba
  error 500 y `limit` no tenía tope: ahora page empieza en 1 y limit va de 1 a 200.
- Seguridad, colectora: "Cerrar sesión" no cerraba la sesión (borraba una cookie vieja de antes de las
  sesiones del servidor y recargaba la misma pantalla con la sesión abierta). Ahora la cierra en el servidor y
  vuelve a la pantalla de ingreso, igual que el panel.
- Colectora: el botón "Modo Escritorio" para volver al panel no aparecía nunca. Ahora se ve para
  administradores y supervisores (el preparador no tiene panel, así que no lo ve).
- En el instructivo, el índice ahora marca el Glosario (la última sección) al llegar al final de la página.
- El modo ayuda no se veía: le faltaban los estilos, así que la explicación aparecía fuera de la pantalla y no
  se marcaba qué tenía ayuda. Ahora marca los elementos con ayuda, muestra la explicación junto al elemento y
  suma un recuadro "Ayuda de esta pantalla" con los pasos principales y un enlace a esa parte del
  instructivo. Funciona tocando en pantallas táctiles y se cierra con Esc.

### Cambiado
- Mercado Libre: la búsqueda de publicaciones tiene un campo por dato (publicación MLA, SKU, título, cuenta,
  estado en ML y situación) en lugar de un solo buscador; se pueden combinar y alcanza con una parte. API:
  `GET /api/admin/sales-channels/{id}/listings` acepta `sku`, `listing`, `title`, `account` y `status`
  (opcionales); `q` y `problem` siguen igual.
- Auditoría de Inventario: al entrar muestra solo las sesiones de conteo de hoy. Para ver otras se busca por
  fechas, sucursal, sector, estado (abierta, en revisión, cerrada), modalidad (HOT o COLD) y operador; el
  botón "Hoy" vuelve a las del día. API: `GET /api/inventory/sessions` acepta esos filtros (opcionales) y
  `limit`; sin filtros devuelve todas, como antes (las usa la colectora).
- Pedidos: la pantalla ya no lista los últimos 100 al entrar. Se busca con los atajos "Para empacar", "De hoy"
  y "Urgentes", o por número de pedido o de venta del canal, cliente o comprador, SKU, estado, canal, cuenta,
  tipo de envío, urgente y fechas. Cada pedido muestra la fecha, URGENTE y, si vino de un canal, el canal, la
  venta y el tipo de envío; los estados se ven en castellano. API: `GET /api/admin/documents` acepta esos
  filtros (opcionales) y `limit`, y suma `created_at`, `external_ref`, `external_account`, `shipping_type`,
  `urgent` y `channel_code`; sin filtros responde igual que antes.
- Compras y Recepción: en el menú se despliega como Reportes, con un módulo por tipo (Órdenes de Compra,
  Remitos de Entrada, Facturas, Traspasos y Devoluciones). Cada historial no lista nada hasta buscar y tiene
  sus propios filtros (número, proveedor o cliente, sucursal o sector, OC, estado y fechas); el botón "Nuevo"
  del encabezado abre el formulario de alta (el supervisor no lo ve). Facturas y Devoluciones por ahora solo
  se consultan: su carga desde el panel sigue en preparación. Los atajos del tablero y "Crear ODT" de
  reabastecimiento abren el módulo correspondiente. API: los historiales (`purchase-orders`,
  `purchase-remitos`, `purchase-invoices`, `transfer-orders`, `returns`) aceptan filtros opcionales por campo
  y fechas (`date_from`, `date_to`); `search` sigue igual y `limit` va de 1 a 500.
- Artículos: la pantalla ya no lista todo al entrar. Se busca por SKU, descripción, categoría, ubicación
  asignada, tipo (simple o combo) y stock (con stock, sin stock o negativo); con los campos vacíos, Buscar
  muestra el catálogo por páginas de 25. API: `GET /api/admin/items` acepta `category`, `location`, `combo`
  (SI/NO) y `stock` (CON/SIN/NEGATIVO), todos opcionales.
- Depósitos: la pantalla lista solo las sucursales, con cuántos sectores y ubicaciones tiene cada una. Cada
  sucursal tiene "Configuración" (editar sus datos; solo administrador) y "Sectores", que muestra sus sectores
  y, en cada uno, sus ubicaciones con un buscador. Desde ahí se crean sectores y ubicaciones con la sucursal o
  el sector ya elegidos. Antes se veían juntas las tablas completas de sectores y ubicaciones.
- Clientes y Proveedores: la pantalla ya no lista todo al entrar. Se busca por CUIT/CUIL (con o sin guiones,
  también una parte), razón social, rol y dirección (calle, localidad, código postal o etiqueta); con los campos
  vacíos, Buscar muestra los primeros 200. Al crear o editar se repite la última búsqueda. API:
  `GET /api/admin/entities` acepta los filtros opcionales `tax_id`, `name`, `role`, `address` y `limit`; sin
  filtros responde igual que antes (lo usan los selectores de otras pantallas).
- Configuración: tarjeta nueva "Eventos del sistema" con los últimos 6 registros de la auditoría, marcados
  APROBADO o ERROR (ingresos fallidos, accesos no autorizados, claves de canal inválidas, alertas), y acceso a
  la auditoría completa. En Auditoría General esas acciones se ven en rojo. API: `GET /api/admin/logs` acepta
  `limit` (por defecto 100, máximo 500) y cada registro trae `result` (`OK` o `ERROR`).
- Configuración, Canales de venta: la tarjeta ya no lista todos los canales. Muestra los cuatro más activos
  (más pedidos en los últimos 7 días) y el botón "Ver canales" abre la lista completa. Cada canal tiene los
  botones "Configuración" y "Eventos"; Eventos muestra sus últimos pedidos (venta, cuenta, tipo de envío y
  estado) y los avisos de stock y de estado que Tracker le dejó. API: la lista de canales suma `orders_7d` y
  hay una ruta nueva, `GET /api/admin/sales-channels/{id}/events` (solo administrador).
- El acceso a Mercado Libre del menú lateral usa el isotipo de Mercado Libre (el oficial, en sus colores).
- Política de privacidad y términos actualizados y con el mismo estilo del panel (tema claro y oscuro). Ahora
  describen cómo funciona hoy el sistema: sesiones del lado del servidor (ya no JWT), protección CSRF, datos
  que llegan de los canales de venta (comprador y envío de Mercado Libre), cuánto se guarda cada cosa y el
  contacto de soporte. Los términos indican la licencia correcta, GNU AGPLv3 (decían GPLv3).

## 2026-10-02

### Agregado
- Canales de venta desde el panel (Configuración, Canales de venta): alta, edición (nombre, stock que se
  informa y sucursales), activar o desactivar y rotar la clave. La clave se muestra una sola vez, con un
  botón para copiarla y la explicación de dónde cargarla en el middleware de Mercado Libre.
- Módulo Mercado Libre en el panel: las publicaciones que informa el canal (cuenta, SKU, estado y stock en
  Mercado Libre) junto con el disponible que Tracker les manda, con un resumen de las que se sincronizan
  y de las que no (sin SKU, SKU que no está en Tracker, Full o con error), filtros y búsqueda.
- Pedidos urgentes: un canal de venta puede marcar un pedido como urgente (por ejemplo las ventas Flex de
  Mercado Libre, que se entregan en el día). Los urgentes salen primero en la lista de preparación y en
  las olas de picking, y en la pantalla del preparador se ven con la marca URGENTE.

### Cambiado
- Las ventas Full de un canal (las que salen del depósito del marketplace, como Mercado Libre Full) ahora
  se registran en Tracker con el estado FULL, en vez de rechazarse. Se ven en la lista de pedidos y en
  los reportes del canal, pero no descuentan ni comprometen stock y no entran al picking, al empaque ni a
  devoluciones. El canal las puede cancelar; una cancelación parcial no las manda al picking.

## 2026-10-01

### Agregado
- Canales de venta: Tracker puede conectarse con sistemas externos de venta (el primero va a ser
  Mercado Libre). El administrador da de alta el canal, que recibe su propia clave de acceso, y elige
  cómo se informa el stock: el disponible, o el disponible menos lo ya comprometido en pedidos, y de
  qué sucursales. (`f846161`)
- Los canales de venta pueden cargar pedidos en Tracker: llegan con el número de la venta del canal,
  la cuenta y el envío, se preparan como cualquier pedido y el canal los puede cancelar. Si el mismo
  pedido llega dos veces se registra una sola. En las listas se ve el nombre del comprador. (`3e57033`)
- Los canales de venta pueden consultar el stock disponible de cada artículo según su configuración
  (con o sin lo comprometido en pedidos abiertos, y de las sucursales elegidas). (`ea87670`)
- Los canales de venta reciben los cambios: cuándo cambia el stock de un artículo y cuándo uno de
  sus pedidos se empieza a preparar, queda listo, se despacha o se cancela. (`77caba8`)
- Si el canal de venta manda su etiqueta de envío (por ejemplo la de Mercado Libre), al empacar se
  imprime esa en lugar de la de Tracker. (`beeb3cd`)

### Seguridad
- El navegador ya no ejecuta código JavaScript escrito dentro de las páginas, solo el de los archivos
  del sistema: si alguien lograra meter código en un dato (un nombre, una descripción), no se ejecuta.
  Para el usuario no cambia nada. (`a219a83`, `2b37925`, `0292ea2`)
- Las etiquetas de artículo ya no se pueden romper con caracteres especiales en el código o la
  descripción del artículo (pasaba lo mismo que se había corregido en las etiquetas de pedido). (`eea5cf4`)
- El bloqueo por claves mal ingresadas ahora es por usuario: si alguien se equivoca varias veces,
  se bloquea solo esa cuenta en esa conexión y no el resto del depósito. Tampoco se puede esquivar el
  bloqueo entrando con otra cuenta ni mandando muchos intentos a la vez. (`42c252a`)
- Protección contra CSRF: otra página abierta en el navegador ya no puede hacer operaciones en
  Tracker360 usando tu sesión. Para el usuario no cambia nada. (`5263861`)
- Las sesiones ahora se guardan en el servidor y se pueden cerrar de verdad. "Cerrar sesión" cierra
  solo el dispositivo donde se usa (antes cerraba todos). Cambiar la clave de un usuario,
  desactivarlo o borrarlo cierra todas sus sesiones al instante. Al actualizar, todos los usuarios
  tienen que volver a iniciar sesión una vez. (`b1a4bd9`)

### Corregido
- Empacar y despachar un pedido daba "Error interno del servidor" (desde el 2 de septiembre). (`d11048e`)
- Los avisos a otros sistemas (webhooks de stock y de despacho) salen recién cuando el cambio quedó
  guardado, y si el otro sistema no responde se reintentan solos durante una hora y media. Antes
  podían avisar un cambio que todavía no estaba guardado, o perderse si fallaban. (`ad01c51`)
- Número de pedidos, traspasos, órdenes de compra y devoluciones: si dos personas cargan un documento
  a la vez con el mismo número sugerido, el primero que lo guarda se queda con ese número y el otro
  recibe el siguiente, con un aviso que dice cuál le tocó (antes uno de los dos recibía un error).
  (`6084d38`)
- La impresión de etiquetas de artículo desde la integración (`/api/admin/print-jobs`) usa la
  plantilla de Configuración aunque nunca se haya guardado la Configuración. (`1d0ff7b`)
- El ajuste "duración de la sesión" de Configuración ahora se respeta (antes la sesión duraba
  siempre 4 horas). Vale para las sesiones que se abran después de cambiarlo, entre 5 minutos y 7
  días. (`5cce370`)
- La configuración inicial ya no puede crear dos administradores si se envía el formulario dos
  veces al mismo tiempo: la segunda alta espera y se rechaza. (`dd67505`)

### Cambiado
- Los números de pedidos, órdenes de compra, traspasos y devoluciones los asigna el sistema, en
  orden correlativo, y ya no se pueden escribir a mano. Los de facturas y remitos de compra se siguen
  cargando como hasta ahora. Para integraciones: el campo del número se sigue aceptando pero se
  ignora; la respuesta trae el número asignado. (`3afb6b8`)
- Si un reporte o el kardex fallan por un problema interno, ahora queda registrado con todo el
  detalle para poder revisarlo, y la pantalla muestra "Error interno del servidor". (`1f4f652`)
- Limpieza interna del código (los módulos se importan de una sola forma y las consultas a la
  base reciben todos los valores como parámetros). No cambia nada del
  funcionamiento. (`592d1a3`, `81eb8af`, `ceb40d9`, `250cf33`, `b12f5aa`, `7e0cfe9`)
- La estructura de la base de datos ahora se actualiza con migraciones numeradas: cada cambio se
  aplica una sola vez y queda registrado. Si una actualización de la base falla, el sistema no
  arranca a medias y el error queda en el registro. Las instalaciones existentes se actualizan
  solas al reiniciar, sin perder datos. (`cfe7ea3`, `a7185d3`)
- Una dependencia menos (PyJWT), que ya no se usa. (`09f2d2f`)
- Los avisos y errores internos del servidor (conexión a la base, webhooks, reportes, indicadores)
  quedan en el registro del servidor con su nivel de gravedad y el detalle completo, en el mismo
  formato que el resto. No cambia nada de lo que ve el usuario. (`d4b3b23`)

## 2026-09-30

### Agregado
- **Observaciones en los documentos.** Pedidos y traspasos muestran sus observaciones dentro del
  propio documento (botón "Detalle"), con fecha y autor. Cualquiera que pueda ver el documento
  puede agregar una, incluido el supervisor. Las observaciones no se editan ni se borran, para
  que queden como registro. Las que genere el sistema aparecen marcadas como "Sistema". Remitos
  y órdenes de compra las van a tener cuando se habilite su pantalla. (`d769b71`, `f5d1fe9`)

- **Órdenes de compra.** Ya se pueden emitir desde "Compras y Recepción" (antes el formulario
  no guardaba nada): proveedor, sucursal de recepción con su dirección, número automático y
  artículos. El historial muestra el estado y el botón "Detalle" abre la orden con lo pedido, lo
  recibido y lo pendiente de cada artículo, más sus observaciones. El supervisor puede
  consultarlas pero no emitirlas. (`9d66025`, `5ae20ac`)
- **Dirección de las sucursales.** En "Depósitos" cada sucursal tiene calle, número, localidad y
  código postal, y se puede editar. Es la dirección de entrega de las órdenes de compra. (`7c71d4b`)

- **Remitos de compra.** Ya se pueden registrar desde "Compras y Recepción" (antes el formulario
  no guardaba nada). Primero se elige el proveedor y aparecen sus órdenes de compra pendientes;
  en cada una se indica cuánto llegó. Contra una orden no se puede recibir más de lo pendiente: lo
  que llegó de más se carga en la sección "Artículos", junto con lo que llegó sin orden. Un
  remito puede tomar varias órdenes y una orden puede recibirse en varios remitos. Al registrar,
  cada orden pasa a "Parcial" o "Completada". El stock entra, como hasta ahora, cuando el depósito
  controla el remito con el celular. El historial tiene "Detalle" con el origen de cada artículo y
  las observaciones. (`5b11cd3`, `f9a1840`)

- **Reporte "Diferencias de Recepción" (Reportes).** Muestra, por proveedor y período, lo que faltó
  y lo que sobró en los remitos ya controlados y los artículos que llegaron sin figurar en el
  remito (con su estado: en cuarentena, aprobado o rechazado). Arriba, un resumen por proveedor
  para los reclamos; cada fila abre su remito. Se puede exportar a CSV. (`f2f01bb`, `c5e5967`)
- **Control ciego de remitos en el celular (con segundo control).** El depósito ya no ve cuánto
  dice el remito: escanea lo que llegó y ve lo que lleva contado. Si llega más, el excedente se
  agrega como artículo suelto, con aviso al operario y una observación en el remito. Si llega un
  artículo que no figura en el remito, se registra como "no esperado" y queda en cuarentena: está
  en el depósito pero no se puede vender, preparar ni transferir hasta que un administrador o
  supervisor lo apruebe (pasa a disponible y se suma al remito) o lo rechace (sale del stock para
  devolverlo). El control termina con "Finalizar control" y el remito queda "Ingresado" o
  "Controlado con diferencias", con los faltantes y sobrantes anotados en sus observaciones.
  (`3d5b904`, `2e1731e`, `8a2ada3`)
- **Opción "Trabaja con segundo control de stock en remitos" (Configuración).** En "No" (por
  defecto), al registrar un remito la mercadería suma al stock en ese momento y el remito queda
  "Ingresado". En "Sí", el remito queda "Pendiente de control" y el stock entra cuando el depósito
  lo escanea con el celular. Cada remito sigue el modo que había al registrarlo. (`f1c5173`)
- **Trabaja con lotes (Sí/No).** El ajuste de Configuración (ahora "Trabaja con lotes y
  vencimientos") existía pero no hacía nada. Ahora, si está en "No", ningún formulario pide lote;
  si está en "Sí", todos lo piden, en el panel y en el celular. (`72fecdb`)

### Cambiado
- Tracker360 empieza a usar la librería común de JZTech (`jztech-core`), la misma que JZTravell y
  JZPass: por ahora, el cálculo de la IP real del cliente. Las dependencias quedan fijadas con
  verificación de integridad. No cambia nada del funcionamiento. (`e69f142`, `6477d2f`)
- Las contraseñas se guardan con el esquema común de `jztech-core` (Argon2id con los parámetros
  recomendados). Las actuales siguen funcionando y se actualizan solas en el próximo ingreso;
  nadie tiene que cambiar su clave. (`95da518`)
- Los errores inesperados del servidor quedan registrados con todo su detalle y la pantalla muestra
  un aviso claro ("Error interno del servidor") en vez de fallar sin mensaje. (`6d9c8ab`)
- Las cabeceras de seguridad comunes también salen de `jztech-core`; el navegador ahora solo
  permite usar la cámara (para escanear) y bloquea ubicación y micrófono, que no se usan. (`3ac3c46`)
- Los tests automáticos ya no forman parte del repositorio: se mantienen aparte, fuera del
  código del sistema. No cambia nada del funcionamiento.
- **El número de remito es único por proveedor.** Dos proveedores pueden usar el mismo número
  sin chocar; un mismo proveedor no puede repetirlo. (`1187f4b`)
- En la lista de pedidos, el botón "Participantes" ahora se llama "Detalle" y abre el pedido
  con su avance, sus participantes y sus observaciones. (`f5d1fe9`)

### Corregido
- **Seguridad: la autorización del agente de impresión vence si no se usa en 90 días** (cada uso
  la renueva, así que un agente en uso no la pierde; uno abandonado tiene que volver a
  autorizarse desde el navegador). Además, el agente solo puede confirmar trabajos que siguen
  pendientes. (`cd1adea`)
- **Reportes y traza de artículos: un filtro inválido ya no devuelve todos los datos**; se avisa
  del error. (`e689514`)
- **Los avisos del panel vuelven a verse con su color** (verde, rojo o amarillo según el caso) y con
  la X para cerrarlos. (`f484f1f`)
- **Etiquetas de pedido: ya salen con el número y el destino.** Antes quedaban con el texto
  "{{ORDER_NUM}}" porque no se reemplazaba; además, un nombre de cliente ya no puede alterar la
  etiqueta. (`16c51c4`)
- **Integraciones: el stock informado como disponible ya no incluye la mercadería en
  cuarentena.** (`9789701`)
- **Un identificador mal formado ahora devuelve un error claro** en vez de un error interno del
  servidor. (`e826cf9`)
- **Conteo "en caliente": ya no descuenta dos veces lo que se vendió mientras se contaba.** La
  diferencia ahora tiene en cuenta los movimientos del local abierto entre la foto y el momento en
  que se contó cada artículo, y la pantalla de revisión muestra exactamente lo que se va a
  aplicar. El conteo "en frío" sigue ajustando el stock a lo contado, y lo que no se contó queda
  como está. (`a2866c1`)
- **Conteos: la mercadería en cuarentena ya no altera el ajuste** del stock disponible. (`f625858`)
- **Picking: el stock se descuenta de donde realmente está.** Una ubicación mal escaneada ya no
  descuenta de otra sucursal (se rechaza), no se puede pickear un artículo sin stock salvo que la
  empresa permita stock negativo, y cada pickeo queda registrado en el stock. Con lotes, sale del
  lote que vence primero. Vale para el picking por pedido y por olas. (`7246ad3`)
- **Seguridad: dar de alta un usuario con un nombre existente ya no lo pisa.** Antes le cambiaba
  la contraseña y el rol (por ejemplo, a administrador); ahora avisa que ya existe. El rol tiene
  que ser administrador, supervisor o preparador. (`c4155ec`)
- **Seguridad: la documentación de la API (`/docs`, `/redoc`) ya no es pública**: pide sesión de
  administrador. (`ea7530c`)
- **Seguridad: los errores de los webhooks ya no muestran datos internos** (direcciones o
  puertos del servidor); se ve un mensaje general y el detalle queda en el registro. (`a6fddd4`)
- **Impresión de etiquetas: máximo 5000 por pedido de impresión**, para que un pedido no pueda
  encolar millones de trabajos. (`c25bea5`)
- **Seguridad: el selector de operario del conteo escapa el nombre de usuario.** (`19252ee`)
- **Reportes: los datos de los filtros ya no se piden dos veces** al entrar a un reporte. (`81d0e1c`)
- **Artículos: un SKU con símbolos (`&`, `<`, `'`) ya abre su stock y su edición.** (`1142141`)
- **Picking por ola: ya no se puede pickear un pedido cancelado o despachado** que llegue en la
  ola; el escaneo se rechaza nombrando esos pedidos. (`48655d1`)
- **Traspasos: se respeta el lote y la ubicación de destino de cada línea.** Antes el stock salía
  y entraba sin lote, y el destino se buscaba en cualquier sucursal; ahora, sin indicar destino se
  usa el de la línea, y una ubicación de otro sector se rechaza. (`8cb8b3d`)
- **Devoluciones: una sucursal o sector inválidos ya no mandan la mercadería al primer depósito**:
  se rechazan, y el sector tiene que pertenecer a la sucursal. (`10eabd6`)
- **Arranque del servidor más seguro:** ya no borra y vuelve a crear en cada inicio las
  relaciones de los pedidos con sus clientes (si fallaba, quedaban borradas). (`c984e07`)
- **Seguridad: el selector de sectores de la impresión masiva ya no permite inyectar código**
  con un nombre o código de cola de impresión malicioso. (`41952d3`)
- **Pantallas del panel alineadas en computadora.** Las tarjetas se veían descuadradas: en
  Depósitos y en Compras quedaban todas en la columna izquierda con la derecha vacía, y en Inicio
  y Configuración las columnas terminaban a alturas distintas. Ahora se ordenan en filas
  alineadas que ocupan todo el ancho (probado a 1366 y 1920 px), y una tabla ancha se desplaza
  dentro de su tarjeta en vez de salirse. (`138139d`)
- **Seguridad: las exportaciones a CSV ya no pueden ejecutar fórmulas en Excel.** Un nombre de
  proveedor o artículo que empezara con "=", "+", "-" o "@" (por ejemplo, un enlace malicioso) se
  ejecutaba al abrir el archivo; ahora se exporta como texto. Vale para todos los reportes. De
  paso, las cantidades en 0 ya no salen vacías. (`5558a30`)
- **El stock en cuarentena ya no se puede preparar ni transferir**, ni aparece como ubicación
  sugerida en el picking o la reposición. (`a8bc359`)
- **Acentos rotos en el celular y en partes del panel** (por ejemplo "Permiso de c?mara"): algunos
  archivos estaban guardados en otra codificación. (`d6661e4`)
- **Recepción: si un artículo figura en varias líneas del remito, el escaneo reparte entre ellas**
  (primero las que tienen pendiente) y respeta el lote y la ubicación de cada línea. (`d8e0939`)
- **Formularios con varios artículos: agregar una fila ya no borra lo que se había escrito en las
  anteriores.** Pasaba en traspasos, pedidos manuales, órdenes de compra, remitos y facturas.
  (`bcdfbf9`)
- **Traspasos: ya no se puede transferir más de lo que dice la orden.** El escaneo se rechaza con
  el aviso "Solo faltan N", igual que en el picking. (`c2807e2`)
- **Traspasos: ya no se puede sacar del origen más stock del que hay.** Antes, con 2 unidades en
  la ubicación de origen se podían transferir 5 y el origen quedaba en -3. Ahora se rechaza
  mostrando lo disponible, salvo que esté activada la opción de permitir stock negativo. (`60b6b7c`)
- **Picking: ya no se puede recolectar más de lo que pide el pedido.** Antes, si faltaban 2
  unidades y se escaneaban 50, el sistema las aceptaba: la línea quedaba en 58 de 10 y se
  descontaban 50 del stock. Ahora el escaneo se rechaza con el aviso "Solo faltan N" y no se
  toca ni el pedido ni el stock. Vale para el picking por pedido y por olas. (`2385e63`)
- **Picking: se rechazan las cantidades en cero, negativas o inválidas.** Una cantidad negativa
  restaba de lo recolectado y sumaba stock que no existía. (`2385e63`)
- **Recepción, traspasos, pedidos, conteos y devoluciones: se rechazan las cantidades negativas o
  inválidas.** Antes se aceptaban y desordenaban el stock: un traspaso con una cantidad inválida
  podía dejar el stock de un artículo ilegible y el traspaso como terminado; uno con cantidad
  negativa devolvía mercadería al origen; un conteo podía guardar -1 y el ajuste lo aplicaba al
  stock; se podían crear pedidos con cantidades negativas. En conteos contar 0 sigue siendo
  válido, y en devoluciones un artículo en 0 se sigue tomando como "no devuelto". (`9399036`)
