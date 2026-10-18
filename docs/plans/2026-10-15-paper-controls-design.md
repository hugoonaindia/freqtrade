# Panel Paper

Panel compacto con estado de cuenta, efectivo, variación diaria de cuenta y contador de órdenes visibles. Formulario de estrategia, símbolos y presupuesto; guardar sin operar. GO PAPER inicia ciclo cada 60 segundos; Parar señala fin tras ciclo en curso. Vista previa y ciclo manual disponibles en pausa. Pestañas: señales, posiciones, operaciones y backtest.

Controles serializados con revisión de selección; no se cambian ajustes ni ejecutan ciclos manuales mientras worker siga vivo. Ajustes persistidos fuera del paquete. Errores del worker desactivan autostart. Parada siempre señala evento aunque falle disco. Cerrar ventana termina motor sin cancelar órdenes enviadas.

Usuario eligió ocultar operaciones anteriores, conservando cuenta Alpaca. Corte UTC fijado 2026-10-15T17:51:16.561315+00:00, independiente de arranque. Se filtran órdenes por created_at. Se conservan posiciones, saldo y guardas de duplicados. «Nuevo historial» permite cambiar corte, sin borrar registros del bróker. Se muestran hasta 100 órdenes recientes de cuenta.

Verificación: tests de configuración, persistencia, selección obsoleta, concurrencia, parada, fallos de disco, error del worker, autenticación de rutas y ocultación sin tocar guardas; prueba visual nativa y recompilación Mac.
