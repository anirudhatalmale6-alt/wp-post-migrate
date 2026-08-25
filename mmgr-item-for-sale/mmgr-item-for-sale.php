<?php
/**
 * Plugin Name: MMGR Items For Sale
 * Description: Owner-submitted classified listings - custom post type, front-end submission form with photo upload, listing grid, and automatic monthly clear-out. Replaces the Frontend Publishing Pro + WPBakery setup from the old site with something native that works on an Elementor theme.
 * Version:     1.0.0
 * Author:      Anirudha Talmale
 * License:     GPL-2.0-or-later
 * Text Domain: mmgr-ifs
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'MMGR_IFS_CPT', 'itemforsale' );
define( 'MMGR_IFS_VERSION', '1.0.0' );

/** Days a listing stays up before the monthly clear-out removes it. */
function mmgr_ifs_lifespan_days() {
	return (int) apply_filters( 'mmgr_ifs_lifespan_days', (int) get_option( 'mmgr_ifs_lifespan_days', 30 ) );
}

/** Where submission notifications go. Defaults to the site admin. */
function mmgr_ifs_notify_address() {
	$to = get_option( 'mmgr_ifs_notify_email', '' );
	return sanitize_email( $to ? $to : get_option( 'admin_email' ) );
}

/* -------------------------------------------------------------------------
 * Post type
 * ---------------------------------------------------------------------- */

add_action( 'init', 'mmgr_ifs_register_post_type' );
function mmgr_ifs_register_post_type() {
	register_post_type(
		MMGR_IFS_CPT,
		array(
			'labels'       => array(
				'name'          => __( 'Items for Sale', 'mmgr-ifs' ),
				'singular_name' => __( 'Item for Sale', 'mmgr-ifs' ),
				'add_new_item'  => __( 'Add New Item for Sale', 'mmgr-ifs' ),
				'edit_item'     => __( 'Edit Item for Sale', 'mmgr-ifs' ),
				'search_items'  => __( 'Search Items for Sale', 'mmgr-ifs' ),
				'not_found'     => __( 'No items for sale.', 'mmgr-ifs' ),
			),
			'public'       => true,
			'has_archive'  => true,
			'rewrite'      => array( 'slug' => MMGR_IFS_CPT, 'with_front' => false ),
			'menu_icon'    => 'dashicons-tag',
			'menu_position'=> 21,
			'supports'     => array( 'title', 'editor', 'excerpt', 'thumbnail' ),
			// Exposed to the REST API deliberately: the old site's post type was
			// not, which is precisely why its listings could not be migrated.
			'show_in_rest' => true,
		)
	);
}

/** The submitter's contact details, kept out of the public post body. */
function mmgr_ifs_contact_fields() {
	return array(
		'name'    => __( 'Full Name', 'mmgr-ifs' ),
		'email'   => __( 'Email', 'mmgr-ifs' ),
		'address' => __( 'Home Address in Mar Menor Golf', 'mmgr-ifs' ),
	);
}

/* -------------------------------------------------------------------------
 * Admin: show the contact details on the edit screen
 * ---------------------------------------------------------------------- */

add_action( 'add_meta_boxes', 'mmgr_ifs_add_meta_box' );
function mmgr_ifs_add_meta_box() {
	add_meta_box( 'mmgr-ifs-contact', __( 'Submitted by', 'mmgr-ifs' ),
		'mmgr_ifs_render_meta_box', MMGR_IFS_CPT, 'side' );
}

function mmgr_ifs_render_meta_box( $post ) {
	echo '<table style="width:100%">';
	foreach ( mmgr_ifs_contact_fields() as $key => $label ) {
		$value = get_post_meta( $post->ID, '_mmgr_ifs_' . $key, true );
		printf(
			'<tr><th style="text-align:left;padding:4px 0">%s</th><td style="padding:4px 0">%s</td></tr>',
			esc_html( $label ),
			$value ? esc_html( $value ) : '<em>' . esc_html__( 'not given', 'mmgr-ifs' ) . '</em>'
		);
	}
	$submitted = get_post_meta( $post->ID, '_mmgr_ifs_submitted_ip', true );
	if ( $submitted ) {
		printf( '<tr><th style="text-align:left;padding:4px 0">%s</th><td style="padding:4px 0">%s</td></tr>',
			esc_html__( 'Submitted from', 'mmgr-ifs' ), esc_html( $submitted ) );
	}
	echo '</table>';
}

/* -------------------------------------------------------------------------
 * Submission form
 * ---------------------------------------------------------------------- */

add_shortcode( 'mmgr_item_for_sale_form', 'mmgr_ifs_form_shortcode' );
function mmgr_ifs_form_shortcode() {
	$notice = '';
	if ( isset( $_GET['mmgr_ifs'] ) ) {
		if ( 'sent' === $_GET['mmgr_ifs'] ) {
			$notice = '<div class="mmgr-ifs-notice mmgr-ifs-ok">'
				. esc_html__( 'Thank you. Your item has been sent to the website administrator and will appear once it has been approved.', 'mmgr-ifs' )
				. '</div>';
		} elseif ( 'error' === $_GET['mmgr_ifs'] ) {
			$reason = isset( $_GET['mmgr_reason'] ) ? sanitize_text_field( wp_unslash( $_GET['mmgr_reason'] ) ) : '';
			$notice = '<div class="mmgr-ifs-notice mmgr-ifs-bad">'
				. esc_html( mmgr_ifs_error_text( $reason ) ) . '</div>';
		}
	}

	ob_start();
	echo $notice; // Built entirely from esc_html above.
	?>
	<form class="mmgr-ifs-form" method="post" enctype="multipart/form-data"
	      action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
		<input type="hidden" name="action" value="mmgr_ifs_submit">
		<input type="hidden" name="mmgr_ifs_redirect" value="<?php echo esc_url( get_permalink() ); ?>">
		<?php wp_nonce_field( 'mmgr_ifs_submit', 'mmgr_ifs_nonce' ); ?>
		<?php // Honeypot: a real person never fills this in, bots fill everything. ?>
		<div class="mmgr-ifs-hp" aria-hidden="true">
			<label>Website<input type="text" name="mmgr_ifs_website" tabindex="-1" autocomplete="off"></label>
		</div>

		<?php foreach ( mmgr_ifs_contact_fields() as $key => $label ) : ?>
			<p class="mmgr-ifs-field">
				<label for="mmgr-ifs-<?php echo esc_attr( $key ); ?>"><?php echo esc_html( $label ); ?> <span class="mmgr-ifs-req">*</span></label>
				<input id="mmgr-ifs-<?php echo esc_attr( $key ); ?>"
				       type="<?php echo 'email' === $key ? 'email' : 'text'; ?>"
				       name="mmgr_ifs_<?php echo esc_attr( $key ); ?>" required
				       maxlength="200">
			</p>
		<?php endforeach; ?>

		<p class="mmgr-ifs-field">
			<label for="mmgr-ifs-title"><?php esc_html_e( 'Title', 'mmgr-ifs' ); ?> <span class="mmgr-ifs-req">*</span></label>
			<input id="mmgr-ifs-title" type="text" name="mmgr_ifs_title" required maxlength="200">
		</p>
		<p class="mmgr-ifs-field">
			<label for="mmgr-ifs-content"><?php esc_html_e( 'Item Description', 'mmgr-ifs' ); ?> <span class="mmgr-ifs-req">*</span></label>
			<textarea id="mmgr-ifs-content" name="mmgr_ifs_content" rows="8" required maxlength="5000"></textarea>
		</p>
		<p class="mmgr-ifs-field">
			<label for="mmgr-ifs-excerpt"><?php esc_html_e( 'Excerpt', 'mmgr-ifs' ); ?></label>
			<textarea id="mmgr-ifs-excerpt" name="mmgr_ifs_excerpt" rows="3" maxlength="500"></textarea>
		</p>
		<p class="mmgr-ifs-field">
			<label for="mmgr-ifs-photo"><?php esc_html_e( 'Selling Item', 'mmgr-ifs' ); ?></label>
			<input id="mmgr-ifs-photo" type="file" name="mmgr_ifs_photo" accept="image/jpeg,image/png,image/gif,image/webp">
			<span class="mmgr-ifs-hint"><?php
				printf(
					/* translators: %s: maximum upload size, e.g. 5 MB */
					esc_html__( 'A photograph of the item. JPG, PNG, GIF or WEBP, up to %s.', 'mmgr-ifs' ),
					esc_html( size_format( mmgr_ifs_max_upload_bytes() ) )
				);
			?></span>
		</p>
		<p class="mmgr-ifs-submit">
			<button type="submit"><?php esc_html_e( 'Submit Item', 'mmgr-ifs' ); ?></button>
		</p>
	</form>
	<?php
	return ob_get_clean();
}

function mmgr_ifs_max_upload_bytes() {
	return (int) apply_filters( 'mmgr_ifs_max_upload_bytes', min( 5 * MB_IN_BYTES, wp_max_upload_size() ) );
}

function mmgr_ifs_error_text( $reason ) {
	$map = array(
		'nonce'    => __( 'Your session expired. Please fill the form in again.', 'mmgr-ifs' ),
		'required' => __( 'Please complete every field marked with a star.', 'mmgr-ifs' ),
		'email'    => __( 'That email address does not look right. Please check it.', 'mmgr-ifs' ),
		'toobig'   => __( 'That photograph is too large. Please use a smaller one.', 'mmgr-ifs' ),
		'filetype' => __( 'That file is not an image. Please upload a JPG, PNG, GIF or WEBP.', 'mmgr-ifs' ),
		'upload'   => __( 'The photograph could not be saved. Please try again.', 'mmgr-ifs' ),
		'flood'    => __( 'You have submitted several items already. Please wait a few minutes before sending another.', 'mmgr-ifs' ),
	);
	return isset( $map[ $reason ] ) ? $map[ $reason ] : __( 'Something went wrong. Please try again.', 'mmgr-ifs' );
}

/* -------------------------------------------------------------------------
 * Submission handler
 * ---------------------------------------------------------------------- */

add_action( 'admin_post_nopriv_mmgr_ifs_submit', 'mmgr_ifs_handle_submit' );
add_action( 'admin_post_mmgr_ifs_submit', 'mmgr_ifs_handle_submit' );

function mmgr_ifs_handle_submit() {
	$redirect = isset( $_POST['mmgr_ifs_redirect'] )
		? esc_url_raw( wp_unslash( $_POST['mmgr_ifs_redirect'] ) )
		: home_url( '/' );
	// Never redirect off-site on the strength of a posted field.
	if ( ! $redirect || wp_parse_url( $redirect, PHP_URL_HOST ) !== wp_parse_url( home_url(), PHP_URL_HOST ) ) {
		$redirect = home_url( '/' );
	}

	$fail = function ( $reason ) use ( $redirect ) {
		wp_safe_redirect( add_query_arg(
			array( 'mmgr_ifs' => 'error', 'mmgr_reason' => $reason ), $redirect ) );
		exit;
	};

	if ( ! isset( $_POST['mmgr_ifs_nonce'] )
		|| ! wp_verify_nonce( sanitize_key( wp_unslash( $_POST['mmgr_ifs_nonce'] ) ), 'mmgr_ifs_submit' ) ) {
		$fail( 'nonce' );
	}

	// Honeypot. Report success so a bot has nothing to learn from the response.
	if ( ! empty( $_POST['mmgr_ifs_website'] ) ) {
		wp_safe_redirect( add_query_arg( 'mmgr_ifs', 'sent', $redirect ) );
		exit;
	}

	if ( mmgr_ifs_is_flooding() ) {
		$fail( 'flood' );
	}

	$name    = isset( $_POST['mmgr_ifs_name'] ) ? sanitize_text_field( wp_unslash( $_POST['mmgr_ifs_name'] ) ) : '';
	$email   = isset( $_POST['mmgr_ifs_email'] ) ? sanitize_email( wp_unslash( $_POST['mmgr_ifs_email'] ) ) : '';
	$address = isset( $_POST['mmgr_ifs_address'] ) ? sanitize_text_field( wp_unslash( $_POST['mmgr_ifs_address'] ) ) : '';
	$title   = isset( $_POST['mmgr_ifs_title'] ) ? sanitize_text_field( wp_unslash( $_POST['mmgr_ifs_title'] ) ) : '';
	$excerpt = isset( $_POST['mmgr_ifs_excerpt'] ) ? sanitize_textarea_field( wp_unslash( $_POST['mmgr_ifs_excerpt'] ) ) : '';
	// Submitters are anonymous, so no HTML at all - not even the subset
	// wp_kses_post allows. The description is displayed as plain paragraphs.
	$content = isset( $_POST['mmgr_ifs_content'] ) ? sanitize_textarea_field( wp_unslash( $_POST['mmgr_ifs_content'] ) ) : '';

	if ( '' === $name || '' === $address || '' === $title || '' === $content ) {
		$fail( 'required' );
	}
	if ( ! is_email( $email ) ) {
		$fail( 'email' );
	}

	$post_id = wp_insert_post(
		array(
			'post_type'      => MMGR_IFS_CPT,
			// Always pending. The old site emailed the administrator for
			// approval and nothing went live unreviewed; keep that.
			'post_status'    => 'pending',
			'post_title'     => $title,
			'post_content'   => $content,
			'post_excerpt'   => $excerpt,
			'comment_status' => 'closed',
			'ping_status'    => 'closed',
		),
		true
	);
	if ( is_wp_error( $post_id ) ) {
		$fail( 'unknown' );
	}

	foreach ( array( 'name' => $name, 'email' => $email, 'address' => $address ) as $key => $value ) {
		update_post_meta( $post_id, '_mmgr_ifs_' . $key, $value );
	}
	update_post_meta( $post_id, '_mmgr_ifs_submitted_ip', mmgr_ifs_client_ip() );

	if ( ! empty( $_FILES['mmgr_ifs_photo']['name'] ) ) {
		$attachment_id = mmgr_ifs_handle_photo( $post_id );
		if ( is_wp_error( $attachment_id ) ) {
			// Keep the listing - the administrator can add a photo later - but
			// tell the submitter their picture did not make it.
			wp_safe_redirect( add_query_arg(
				array( 'mmgr_ifs' => 'error', 'mmgr_reason' => $attachment_id->get_error_code() ), $redirect ) );
			exit;
		}
		set_post_thumbnail( $post_id, $attachment_id );
	}

	mmgr_ifs_notify_admin( $post_id, $name, $email, $address );
	mmgr_ifs_record_submission();

	wp_safe_redirect( add_query_arg( 'mmgr_ifs', 'sent', $redirect ) );
	exit;
}

/**
 * Move the uploaded photograph into the media library.
 *
 * Anonymous uploads are the one genuinely dangerous part of this plugin, so the
 * file is checked three ways: the reported size, the real MIME type as read
 * from the file's own bytes by WordPress, and an explicit allow-list. A file
 * named .jpg that is not an image never gets past wp_check_filetype_and_ext().
 */
function mmgr_ifs_handle_photo( $post_id ) {
	require_once ABSPATH . 'wp-admin/includes/file.php';
	require_once ABSPATH . 'wp-admin/includes/media.php';
	require_once ABSPATH . 'wp-admin/includes/image.php';

	$file = $_FILES['mmgr_ifs_photo'];
	if ( ! empty( $file['error'] ) ) {
		return new WP_Error( 'upload' );
	}
	if ( (int) $file['size'] > mmgr_ifs_max_upload_bytes() ) {
		return new WP_Error( 'toobig' );
	}

	$allowed = array(
		'jpg|jpeg|jpe' => 'image/jpeg',
		'png'          => 'image/png',
		'gif'          => 'image/gif',
		'webp'         => 'image/webp',
	);
	$check = wp_check_filetype_and_ext( $file['tmp_name'], $file['name'], $allowed );
	if ( empty( $check['type'] ) || ! in_array( $check['type'], $allowed, true ) ) {
		return new WP_Error( 'filetype' );
	}
	// getimagesize returns false for anything that is not a real image, which
	// catches a script renamed to .jpg that happened to sniff as an image type.
	if ( false === @getimagesize( $file['tmp_name'] ) ) {
		return new WP_Error( 'filetype' );
	}

	add_filter( 'upload_mimes', 'mmgr_ifs_restrict_mimes', 99 );
	$attachment_id = media_handle_upload(
		'mmgr_ifs_photo',
		$post_id,
		array(),
		array( 'test_form' => false, 'mimes' => $allowed )
	);
	remove_filter( 'upload_mimes', 'mmgr_ifs_restrict_mimes', 99 );

	if ( is_wp_error( $attachment_id ) ) {
		return new WP_Error( 'upload' );
	}
	return $attachment_id;
}

function mmgr_ifs_restrict_mimes() {
	return array(
		'jpg|jpeg|jpe' => 'image/jpeg',
		'png'          => 'image/png',
		'gif'          => 'image/gif',
		'webp'         => 'image/webp',
	);
}

function mmgr_ifs_client_ip() {
	$ip = isset( $_SERVER['REMOTE_ADDR'] ) ? wp_unslash( $_SERVER['REMOTE_ADDR'] ) : '';
	return filter_var( $ip, FILTER_VALIDATE_IP ) ? $ip : '';
}

/** Five submissions per address per hour is generous for a human, hostile to a script. */
function mmgr_ifs_is_flooding() {
	$key = 'mmgr_ifs_rate_' . md5( mmgr_ifs_client_ip() );
	return (int) get_transient( $key ) >= (int) apply_filters( 'mmgr_ifs_rate_limit', 5 );
}

function mmgr_ifs_record_submission() {
	$key   = 'mmgr_ifs_rate_' . md5( mmgr_ifs_client_ip() );
	$count = (int) get_transient( $key );
	set_transient( $key, $count + 1, HOUR_IN_SECONDS );
}

function mmgr_ifs_notify_admin( $post_id, $name, $email, $address ) {
	$edit = admin_url( 'post.php?post=' . (int) $post_id . '&action=edit' );
	$subject = sprintf(
		/* translators: %s: item title */
		__( '[%1$s] New item for sale: %2$s', 'mmgr-ifs' ),
		wp_specialchars_decode( get_bloginfo( 'name' ), ENT_QUOTES ),
		get_the_title( $post_id )
	);
	$lines = array(
		__( 'A new item has been submitted for the For Sale page and is waiting for approval.', 'mmgr-ifs' ),
		'',
		sprintf( __( 'Title:   %s', 'mmgr-ifs' ), get_the_title( $post_id ) ),
		sprintf( __( 'Name:    %s', 'mmgr-ifs' ), $name ),
		sprintf( __( 'Email:   %s', 'mmgr-ifs' ), $email ),
		sprintf( __( 'Address: %s', 'mmgr-ifs' ), $address ),
		'',
		__( 'Review and publish it here:', 'mmgr-ifs' ),
		$edit,
	);
	wp_mail(
		mmgr_ifs_notify_address(),
		$subject,
		implode( "\n", $lines ),
		array( 'Reply-To: ' . $name . ' <' . $email . '>' )
	);
}

/* -------------------------------------------------------------------------
 * Listing grid
 * ---------------------------------------------------------------------- */

add_shortcode( 'mmgr_items_for_sale', 'mmgr_ifs_grid_shortcode' );
function mmgr_ifs_grid_shortcode( $atts ) {
	$atts = shortcode_atts(
		array( 'max' => 50, 'columns' => 4 ),
		$atts,
		'mmgr_items_for_sale'
	);
	$columns = max( 1, min( 6, (int) $atts['columns'] ) );

	$items = new WP_Query(
		array(
			'post_type'           => MMGR_IFS_CPT,
			'post_status'         => 'publish',
			'posts_per_page'      => max( 1, (int) $atts['max'] ),
			'ignore_sticky_posts' => true,
			'no_found_rows'       => true,
		)
	);
	if ( ! $items->have_posts() ) {
		return '<p class="mmgr-ifs-empty">' . esc_html__( 'There are no items for sale at the moment.', 'mmgr-ifs' ) . '</p>';
	}

	ob_start();
	printf( '<div class="mmgr-ifs-grid mmgr-ifs-cols-%d">', (int) $columns );
	while ( $items->have_posts() ) {
		$items->the_post();
		?>
		<article class="mmgr-ifs-item">
			<a class="mmgr-ifs-thumb" href="<?php the_permalink(); ?>">
				<?php
				if ( has_post_thumbnail() ) {
					the_post_thumbnail( 'medium' );
				} else {
					echo '<span class="mmgr-ifs-nophoto" aria-hidden="true"></span>';
				}
				?>
			</a>
			<h3 class="mmgr-ifs-title"><a href="<?php the_permalink(); ?>"><?php the_title(); ?></a></h3>
			<div class="mmgr-ifs-excerpt"><?php echo esc_html( wp_trim_words( get_the_excerpt(), 22 ) ); ?></div>
		</article>
		<?php
	}
	echo '</div>';
	wp_reset_postdata();
	return ob_get_clean();
}

/* -------------------------------------------------------------------------
 * Styles
 * ---------------------------------------------------------------------- */

add_action( 'wp_enqueue_scripts', 'mmgr_ifs_styles' );
function mmgr_ifs_styles() {
	wp_register_style( 'mmgr-ifs', false, array(), MMGR_IFS_VERSION );
	wp_enqueue_style( 'mmgr-ifs' );
	wp_add_inline_style( 'mmgr-ifs', '
.mmgr-ifs-grid{display:grid;gap:24px;margin:0 0 32px}
.mmgr-ifs-cols-2{grid-template-columns:repeat(2,1fr)}
.mmgr-ifs-cols-3{grid-template-columns:repeat(3,1fr)}
.mmgr-ifs-cols-4{grid-template-columns:repeat(4,1fr)}
.mmgr-ifs-item{min-width:0}
.mmgr-ifs-thumb{display:block;margin-bottom:10px}
.mmgr-ifs-thumb img{width:100%;height:auto;display:block}
.mmgr-ifs-nophoto{display:block;width:100%;padding-top:70%;background:#eee}
.mmgr-ifs-title{font-size:1.05rem;margin:0 0 6px;line-height:1.3}
.mmgr-ifs-excerpt{font-size:.9rem;opacity:.8}
.mmgr-ifs-form{max-width:640px}
.mmgr-ifs-field{display:flex;flex-direction:column;margin:0 0 18px}
.mmgr-ifs-field label{font-weight:600;margin-bottom:6px}
.mmgr-ifs-field input[type=text],.mmgr-ifs-field input[type=email],.mmgr-ifs-field textarea{
width:100%;padding:10px;border:1px solid #ccc;border-radius:3px;font:inherit}
.mmgr-ifs-req{color:#c00}
.mmgr-ifs-hint{font-size:.85rem;opacity:.75;margin-top:6px}
.mmgr-ifs-hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
.mmgr-ifs-notice{padding:12px 16px;margin:0 0 20px;border-left:4px solid}
.mmgr-ifs-ok{background:#eef8ee;border-color:#3a3}
.mmgr-ifs-bad{background:#fdeeee;border-color:#c33}
@media(max-width:900px){.mmgr-ifs-cols-3,.mmgr-ifs-cols-4{grid-template-columns:repeat(2,1fr)}}
@media(max-width:560px){.mmgr-ifs-grid{grid-template-columns:1fr}}
' );
}

/* -------------------------------------------------------------------------
 * Monthly clear-out
 * ---------------------------------------------------------------------- */

register_activation_hook( __FILE__, 'mmgr_ifs_activate' );
function mmgr_ifs_activate() {
	mmgr_ifs_register_post_type();
	flush_rewrite_rules();
	if ( ! wp_next_scheduled( 'mmgr_ifs_cleanup' ) ) {
		wp_schedule_event( time() + HOUR_IN_SECONDS, 'daily', 'mmgr_ifs_cleanup' );
	}
}

register_deactivation_hook( __FILE__, 'mmgr_ifs_deactivate' );
function mmgr_ifs_deactivate() {
	wp_clear_scheduled_hook( 'mmgr_ifs_cleanup' );
	flush_rewrite_rules();
}

add_action( 'mmgr_ifs_cleanup', 'mmgr_ifs_run_cleanup' );
/**
 * Trash listings older than the configured lifespan.
 *
 * Runs daily rather than monthly so an item lives for its full term regardless
 * of when it was posted - a monthly sweep would delete something submitted the
 * day before. Items are trashed, not destroyed, so a mistake is recoverable.
 *
 * @return int Number of listings retired.
 */
function mmgr_ifs_run_cleanup() {
	$days = mmgr_ifs_lifespan_days();
	if ( $days < 1 ) {
		return 0;
	}
	$old = get_posts(
		array(
			'post_type'      => MMGR_IFS_CPT,
			'post_status'    => array( 'publish', 'pending' ),
			'posts_per_page' => 100,
			'fields'         => 'ids',
			'date_query'     => array(
				array( 'before' => $days . ' days ago' ),
			),
		)
	);
	foreach ( $old as $id ) {
		wp_trash_post( $id );
	}
	return count( $old );
}

/* -------------------------------------------------------------------------
 * Settings
 * ---------------------------------------------------------------------- */

add_action( 'admin_init', 'mmgr_ifs_settings' );
function mmgr_ifs_settings() {
	register_setting( 'mmgr_ifs', 'mmgr_ifs_notify_email', array(
		'type' => 'string', 'sanitize_callback' => 'sanitize_email', 'default' => '' ) );
	register_setting( 'mmgr_ifs', 'mmgr_ifs_lifespan_days', array(
		'type' => 'integer', 'sanitize_callback' => 'absint', 'default' => 30 ) );
}

add_action( 'admin_menu', 'mmgr_ifs_settings_page' );
function mmgr_ifs_settings_page() {
	add_submenu_page( 'edit.php?post_type=' . MMGR_IFS_CPT,
		__( 'For Sale Settings', 'mmgr-ifs' ), __( 'Settings', 'mmgr-ifs' ),
		'manage_options', 'mmgr-ifs-settings', 'mmgr_ifs_render_settings' );
}

function mmgr_ifs_render_settings() {
	if ( ! current_user_can( 'manage_options' ) ) {
		return;
	}
	?>
	<div class="wrap">
		<h1><?php esc_html_e( 'Items For Sale', 'mmgr-ifs' ); ?></h1>
		<form method="post" action="options.php">
			<?php settings_fields( 'mmgr_ifs' ); ?>
			<table class="form-table" role="presentation">
				<tr>
					<th scope="row"><label for="mmgr_ifs_notify_email"><?php esc_html_e( 'Notification email', 'mmgr-ifs' ); ?></label></th>
					<td>
						<input name="mmgr_ifs_notify_email" id="mmgr_ifs_notify_email" type="email" class="regular-text"
						       value="<?php echo esc_attr( get_option( 'mmgr_ifs_notify_email', '' ) ); ?>"
						       placeholder="<?php echo esc_attr( get_option( 'admin_email' ) ); ?>">
						<p class="description"><?php esc_html_e( 'Where new submissions are sent. Leave blank to use the site administrator address.', 'mmgr-ifs' ); ?></p>
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="mmgr_ifs_lifespan_days"><?php esc_html_e( 'Remove listings after', 'mmgr-ifs' ); ?></label></th>
					<td>
						<input name="mmgr_ifs_lifespan_days" id="mmgr_ifs_lifespan_days" type="number" min="0" step="1" class="small-text"
						       value="<?php echo esc_attr( mmgr_ifs_lifespan_days() ); ?>">
						<?php esc_html_e( 'days', 'mmgr-ifs' ); ?>
						<p class="description"><?php esc_html_e( 'Listings older than this are moved to the trash automatically. Set to 0 to keep them indefinitely.', 'mmgr-ifs' ); ?></p>
					</td>
				</tr>
			</table>
			<?php submit_button(); ?>
		</form>
		<h2><?php esc_html_e( 'Adding it to a page', 'mmgr-ifs' ); ?></h2>
		<p><?php esc_html_e( 'Put these two shortcodes on the For Sale page - the grid first, the form below it, matching the old site:', 'mmgr-ifs' ); ?></p>
		<p><code>[mmgr_items_for_sale max="50" columns="4"]</code><br>
		   <code>[mmgr_item_for_sale_form]</code></p>
	</div>
	<?php
}
