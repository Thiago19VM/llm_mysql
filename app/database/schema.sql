-- MySQL dump 10.13  Distrib 8.0.45, for Win64 (x86_64)
--
-- Host: localhost    Database: literaweb
-- ------------------------------------------------------
-- Server version	8.0.45

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `exemplares`
--

DROP TABLE IF EXISTS `exemplares`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `exemplares` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `livro_id` bigint unsigned NOT NULL,
  `codigo_exemplar` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` enum('disponivel','reservado','emprestado','perdido') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'disponivel',
  `localizacao` varchar(120) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `ativo` tinyint(1) NOT NULL DEFAULT '1',
  `criado_em` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `atualizado_em` timestamp NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
  `excluido_em` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_exemplares_codigo` (`codigo_exemplar`),
  KEY `idx_exemplares_livro` (`livro_id`),
  KEY `idx_exemplares_status` (`status`),
  CONSTRAINT `fk_exemplares_livro` FOREIGN KEY (`livro_id`) REFERENCES `livros` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=801 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `forum_comentario_reacoes`
--

DROP TABLE IF EXISTS `forum_comentario_reacoes`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `forum_comentario_reacoes` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `comentario_id` bigint unsigned NOT NULL,
  `usuario_id` bigint unsigned NOT NULL,
  `emoji` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `criado_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_forum_comentario_reacao` (`comentario_id`,`usuario_id`,`emoji`),
  UNIQUE KEY `uq_reacao_comentario_usuario` (`comentario_id`,`usuario_id`),
  KEY `ix_forum_comentario_reacoes_comentario` (`comentario_id`),
  KEY `ix_forum_comentario_reacoes_emoji` (`emoji`),
  KEY `ix_forum_comentario_reacoes_usuario` (`usuario_id`),
  CONSTRAINT `fk_forum_comentario_reacoes_comentario` FOREIGN KEY (`comentario_id`) REFERENCES `forum_comentarios` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_forum_comentario_reacoes_usuario` FOREIGN KEY (`usuario_id`) REFERENCES `usuarios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `forum_comentario_upvotes`
--

DROP TABLE IF EXISTS `forum_comentario_upvotes`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `forum_comentario_upvotes` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `comentario_id` bigint unsigned NOT NULL,
  `usuario_id` bigint unsigned NOT NULL,
  `criado_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_forum_comentario_upvote` (`comentario_id`,`usuario_id`),
  KEY `ix_forum_comentario_upvotes_comentario` (`comentario_id`),
  KEY `ix_forum_comentario_upvotes_usuario` (`usuario_id`),
  CONSTRAINT `fk_forum_comentario_upvotes_comentario` FOREIGN KEY (`comentario_id`) REFERENCES `forum_comentarios` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_forum_comentario_upvotes_usuario` FOREIGN KEY (`usuario_id`) REFERENCES `usuarios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `forum_comentarios`
--

DROP TABLE IF EXISTS `forum_comentarios`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `forum_comentarios` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `topico_id` bigint unsigned NOT NULL,
  `autor_id` bigint unsigned NOT NULL,
  `parent_id` bigint unsigned DEFAULT NULL,
  `conteudo` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `oculto` tinyint(1) NOT NULL DEFAULT '0',
  `criado_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `atualizado_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `upvotes_count` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `ix_forum_comentarios_topico_id` (`topico_id`),
  KEY `ix_forum_comentarios_autor_id` (`autor_id`),
  KEY `ix_forum_comentarios_parent_id` (`parent_id`),
  KEY `ix_forum_comentarios_criado_em` (`criado_em`),
  KEY `ix_forum_comentarios_topico_parent` (`topico_id`,`parent_id`,`oculto`,`criado_em`),
  CONSTRAINT `fk_forum_comentarios_autor` FOREIGN KEY (`autor_id`) REFERENCES `usuarios` (`id`),
  CONSTRAINT `fk_forum_comentarios_parent` FOREIGN KEY (`parent_id`) REFERENCES `forum_comentarios` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_forum_comentarios_topico` FOREIGN KEY (`topico_id`) REFERENCES `forum_topicos` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `forum_moderacao_logs`
--

DROP TABLE IF EXISTS `forum_moderacao_logs`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `forum_moderacao_logs` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `topico_id` bigint unsigned NOT NULL,
  `moderador_id` int unsigned NOT NULL,
  `autor_id` bigint unsigned NOT NULL,
  `acao` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL,
  `motivo` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `detalhes` text COLLATE utf8mb4_unicode_ci,
  `criado_em` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_forum_moderacao_logs_topico_id` (`topico_id`),
  KEY `ix_forum_moderacao_logs_moderador_id` (`moderador_id`),
  KEY `ix_forum_moderacao_logs_autor_id` (`autor_id`),
  KEY `ix_forum_moderacao_logs_acao` (`acao`),
  KEY `ix_forum_moderacao_logs_criado_em` (`criado_em`),
  CONSTRAINT `fk_forum_moderacao_logs_autor` FOREIGN KEY (`autor_id`) REFERENCES `usuarios` (`id`),
  CONSTRAINT `fk_forum_moderacao_logs_moderador` FOREIGN KEY (`moderador_id`) REFERENCES `funcionarios` (`id`),
  CONSTRAINT `fk_forum_moderacao_logs_topico` FOREIGN KEY (`topico_id`) REFERENCES `forum_topicos` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `forum_presencas`
--

DROP TABLE IF EXISTS `forum_presencas`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `forum_presencas` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `usuario_id` bigint unsigned NOT NULL,
  `last_seen` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_forum_presencas_usuario` (`usuario_id`),
  KEY `ix_forum_presencas_last_seen` (`last_seen`),
  CONSTRAINT `fk_forum_presencas_usuario` FOREIGN KEY (`usuario_id`) REFERENCES `usuarios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=133 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `forum_seguidores`
--

DROP TABLE IF EXISTS `forum_seguidores`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `forum_seguidores` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `seguidor_id` bigint unsigned NOT NULL,
  `seguido_id` bigint unsigned NOT NULL,
  `criado_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'aceito',
  `respondido_em` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_forum_seguidores` (`seguidor_id`,`seguido_id`),
  KEY `ix_forum_seguidores_seguido` (`seguido_id`),
  KEY `ix_forum_seguidores_seguidor` (`seguidor_id`),
  KEY `ix_forum_seguidores_status` (`status`),
  CONSTRAINT `fk_forum_seguidores_seguido` FOREIGN KEY (`seguido_id`) REFERENCES `usuarios` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_forum_seguidores_seguidor` FOREIGN KEY (`seguidor_id`) REFERENCES `usuarios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `forum_topico_upvotes`
--

DROP TABLE IF EXISTS `forum_topico_upvotes`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `forum_topico_upvotes` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `topico_id` bigint unsigned NOT NULL,
  `usuario_id` bigint unsigned NOT NULL,
  `criado_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_forum_topico_upvote` (`topico_id`,`usuario_id`),
  KEY `ix_forum_topico_upvotes_topico` (`topico_id`),
  KEY `ix_forum_topico_upvotes_usuario` (`usuario_id`),
  CONSTRAINT `fk_forum_topico_upvotes_topico` FOREIGN KEY (`topico_id`) REFERENCES `forum_topicos` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_forum_topico_upvotes_usuario` FOREIGN KEY (`usuario_id`) REFERENCES `usuarios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `forum_topico_views`
--

DROP TABLE IF EXISTS `forum_topico_views`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `forum_topico_views` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `topico_id` bigint unsigned NOT NULL,
  `usuario_id` bigint unsigned NOT NULL,
  `criado_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_forum_topico_views` (`topico_id`,`usuario_id`),
  KEY `ix_forum_topico_views_topico` (`topico_id`),
  KEY `fk_ftv_usuario` (`usuario_id`),
  CONSTRAINT `fk_ftv_topico` FOREIGN KEY (`topico_id`) REFERENCES `forum_topicos` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_ftv_usuario` FOREIGN KEY (`usuario_id`) REFERENCES `usuarios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=84 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `forum_topicos`
--

DROP TABLE IF EXISTS `forum_topicos`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `forum_topicos` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `titulo` varchar(180) COLLATE utf8mb4_unicode_ci NOT NULL,
  `slug` varchar(220) COLLATE utf8mb4_unicode_ci NOT NULL,
  `conteudo` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `livro_id` bigint unsigned DEFAULT NULL,
  `autor_id` bigint unsigned NOT NULL,
  `fixado` tinyint(1) NOT NULL DEFAULT '0',
  `trancado` tinyint(1) NOT NULL DEFAULT '0',
  `oculto` tinyint(1) NOT NULL DEFAULT '0',
  `visualizacoes` int NOT NULL DEFAULT '0',
  `comentarios_count` int NOT NULL DEFAULT '0',
  `criado_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `atualizado_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `ultimo_comentario_em` datetime DEFAULT NULL,
  `upvotes_count` int NOT NULL DEFAULT '0',
  `excluido` tinyint(1) NOT NULL DEFAULT '0',
  `excluido_em` datetime DEFAULT NULL,
  `excluido_por_id` int unsigned DEFAULT NULL,
  `motivo_exclusao` text COLLATE utf8mb4_unicode_ci,
  `ocultado_em` datetime DEFAULT NULL,
  `ocultado_por_id` int unsigned DEFAULT NULL,
  `motivo_ocultacao` text COLLATE utf8mb4_unicode_ci,
  `trancado_em` datetime DEFAULT NULL,
  `trancado_por_id` int unsigned DEFAULT NULL,
  `motivo_trancamento` text COLLATE utf8mb4_unicode_ci,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_forum_topicos_slug` (`slug`),
  KEY `ix_forum_topicos_slug` (`slug`),
  KEY `ix_forum_topicos_livro_id` (`livro_id`),
  KEY `ix_forum_topicos_autor_id` (`autor_id`),
  KEY `ix_forum_topicos_criado_em` (`criado_em`),
  KEY `ix_forum_topicos_ultimo_comentario_em` (`ultimo_comentario_em`),
  KEY `ix_forum_topicos_atividade` (`fixado`,`oculto`,`ultimo_comentario_em`,`criado_em`),
  KEY `ix_forum_topicos_excluido` (`excluido`),
  KEY `ix_forum_topicos_excluido_por_id` (`excluido_por_id`),
  KEY `ix_forum_topicos_ocultado_por_id` (`ocultado_por_id`),
  KEY `ix_forum_topicos_trancado_por_id` (`trancado_por_id`),
  CONSTRAINT `fk_forum_topicos_autor` FOREIGN KEY (`autor_id`) REFERENCES `usuarios` (`id`),
  CONSTRAINT `fk_forum_topicos_excluido_por` FOREIGN KEY (`excluido_por_id`) REFERENCES `funcionarios` (`id`),
  CONSTRAINT `fk_forum_topicos_livro` FOREIGN KEY (`livro_id`) REFERENCES `livros` (`id`),
  CONSTRAINT `fk_forum_topicos_ocultado_por` FOREIGN KEY (`ocultado_por_id`) REFERENCES `funcionarios` (`id`),
  CONSTRAINT `fk_forum_topicos_trancado_por` FOREIGN KEY (`trancado_por_id`) REFERENCES `funcionarios` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `funcionarios`
--

DROP TABLE IF EXISTS `funcionarios`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `funcionarios` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `nome` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email` varchar(120) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hash_senha` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `ativo` tinyint(1) NOT NULL DEFAULT '1',
  `criado_em` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_funcionarios_email` (`email`),
  KEY `idx_funcionarios_email` (`email`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `livros`
--

DROP TABLE IF EXISTS `livros`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `livros` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `titulo` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `autor` varchar(180) COLLATE utf8mb4_unicode_ci NOT NULL,
  `genero` varchar(120) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `editora` varchar(180) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `ano` smallint unsigned DEFAULT NULL,
  `isbn` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `capa_arquivo` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `tipo_capa` enum('Capa Comum','Livro de Bolso') COLLATE utf8mb4_unicode_ci NOT NULL,
  `sinopse` text COLLATE utf8mb4_unicode_ci,
  `criado_em` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `atualizado_em` timestamp NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
  `edicao` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `excluido_em` datetime DEFAULT NULL,
  `ativo` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_livros_isbn_tipo` (`isbn`,`tipo_capa`),
  KEY `idx_livros_titulo` (`titulo`),
  KEY `idx_livros_autor` (`autor`),
  KEY `idx_livros_genero` (`genero`)
) ENGINE=InnoDB AUTO_INCREMENT=41 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `notificacoes`
--

DROP TABLE IF EXISTS `notificacoes`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `notificacoes` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `usuario_id` bigint unsigned NOT NULL,
  `ator_id` bigint unsigned DEFAULT NULL,
  `tipo` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `titulo` varchar(160) COLLATE utf8mb4_unicode_ci NOT NULL,
  `mensagem` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `link` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `lida` tinyint(1) NOT NULL DEFAULT '0',
  `criada_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_notif_usuario_id` (`usuario_id`),
  KEY `idx_notif_ator_id` (`ator_id`),
  KEY `idx_notif_tipo` (`tipo`),
  KEY `idx_notif_lida` (`lida`),
  KEY `idx_notif_criada_em` (`criada_em`),
  CONSTRAINT `fk_notif_ator` FOREIGN KEY (`ator_id`) REFERENCES `usuarios` (`id`),
  CONSTRAINT `fk_notif_usuario` FOREIGN KEY (`usuario_id`) REFERENCES `usuarios` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=12 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `politicas_emprestimo`
--

DROP TABLE IF EXISTS `politicas_emprestimo`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `politicas_emprestimo` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `nome` varchar(120) NOT NULL,
  `observacao` varchar(255) DEFAULT NULL,
  `prazo_devolucao_dias` int NOT NULL DEFAULT '14',
  `prazo_retirada_horas` int NOT NULL DEFAULT '48',
  `limite_reservas_ativas` int NOT NULL DEFAULT '999',
  `max_renovacoes` int NOT NULL DEFAULT '1',
  `prazo_renovacao_dias` int NOT NULL DEFAULT '14',
  `tolerancia_atraso_dias` int NOT NULL DEFAULT '0',
  `tipo_bloqueio_atraso` varchar(20) NOT NULL DEFAULT '1_por_1',
  `valor_bloqueio_atraso` int NOT NULL DEFAULT '1',
  `ativa` tinyint(1) NOT NULL DEFAULT '1',
  `vigente_desde` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `criada_em` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `criada_por` bigint unsigned DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=6 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `reservas`
--

DROP TABLE IF EXISTS `reservas`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `reservas` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `usuario_id` bigint unsigned NOT NULL,
  `exemplar_id` bigint unsigned NOT NULL,
  `tipo` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'retirada',
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pendente',
  `cep` varchar(12) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `rua` varchar(180) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `numero` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `bairro` varchar(120) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `cidade` varchar(120) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `uf` varchar(2) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `complemento` varchar(180) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `observacao` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `codigo_rastreio` varchar(80) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `criado_em` datetime DEFAULT NULL,
  `retirar_ate` datetime DEFAULT NULL,
  `emprestado_em` datetime DEFAULT NULL,
  `devolver_ate` date DEFAULT NULL,
  `devolvido_em` datetime DEFAULT NULL,
  `renovacoes` int NOT NULL DEFAULT '0',
  `politica_id` bigint unsigned DEFAULT NULL,
  `prazo_devolucao_dias_aplicado` int DEFAULT NULL,
  `prazo_retirada_horas_aplicado` int DEFAULT NULL,
  `limite_reservas_ativas_aplicado` int DEFAULT NULL,
  `max_renovacoes_aplicado` int DEFAULT NULL,
  `prazo_renovacao_dias_aplicado` int DEFAULT NULL,
  `tolerancia_atraso_dias_aplicada` int DEFAULT NULL,
  `tipo_bloqueio_atraso_aplicado` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `valor_bloqueio_atraso_aplicado` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_reservas_usuario_id` (`usuario_id`),
  KEY `ix_reservas_exemplar_id` (`exemplar_id`),
  KEY `fk_reservas_politica` (`politica_id`),
  CONSTRAINT `fk_reservas_exemplar` FOREIGN KEY (`exemplar_id`) REFERENCES `exemplares` (`id`),
  CONSTRAINT `fk_reservas_politica` FOREIGN KEY (`politica_id`) REFERENCES `politicas_emprestimo` (`id`),
  CONSTRAINT `fk_reservas_usuario` FOREIGN KEY (`usuario_id`) REFERENCES `usuarios` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=62 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `usuarios`
--

DROP TABLE IF EXISTS `usuarios`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `usuarios` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `nome_usuario` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email` varchar(190) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hash_senha` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `genero` enum('F','M') COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `data_nascimento` date DEFAULT NULL,
  `criado_em` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `google_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `telefone` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `cep` varchar(12) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `rua` varchar(180) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `numero` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `bairro` varchar(120) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `cidade` varchar(120) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `uf` char(2) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `complemento` varchar(180) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `foto` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `bio` varchar(280) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `bloqueado_ate` datetime DEFAULT NULL,
  `forum_privacidade` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'todos',
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'ativo',
  `ultimo_ativo_em` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_usuarios_email` (`email`),
  UNIQUE KEY `uq_usuarios_nome_usuario` (`nome_usuario`),
  UNIQUE KEY `google_id` (`google_id`),
  KEY `ix_usuarios_status` (`status`),
  KEY `ix_usuarios_ultimo_ativo_em` (`ultimo_ativo_em`)
) ENGINE=InnoDB AUTO_INCREMENT=7 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-07-15 15:15:17
