# Notas de los parches

Cambios de Tracker360, del más nuevo al más viejo. Cada entrada corresponde a un push a `main`.

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

### Cambiado
- En la lista de pedidos, el botón "Participantes" ahora se llama "Detalle" y abre el pedido
  con su avance, sus participantes y sus observaciones. (`f5d1fe9`)

### Corregido
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
