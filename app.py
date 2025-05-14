from flask import Flask, render_template, request, redirect, session
from neo4j import GraphDatabase
import requests
from werkzeug.security import generate_password_hash, check_password_hash  


app = Flask(__name__)
app.secret_key= 'cok_gizli_anahtar'

uri = "bolt://localhost:7687"  
user = "neo4j"
password = "123123yk"
driver = GraphDatabase.driver(uri, auth=(user, password))


TMDB_API_KEY = "f937deade31ade1eff82323043246bc0"

def get_movie_details_from_tmdb(title):
    url = f"https://api.themoviedb.org/3/search/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "query": title,
        "language": "tr-TR"
    }

    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        if data["results"]:
            movie = data["results"][0]

            # Fragman bilgisi için ek sorgu
            movie_id = movie.get("id")
            trailer_url = None

            video_url = f"https://api.themoviedb.org/3/movie/{movie_id}/videos"
            video_params = {"api_key": TMDB_API_KEY, "language": "tr-TR"}
            video_response = requests.get(video_url, params=video_params)

            if video_response.status_code == 200:
                video_data = video_response.json()
                for video in video_data.get("results", []):
                    if video["site"] == "YouTube" and video["type"] == "Trailer":
                        trailer_url = f"https://www.youtube.com/watch?v={video['key']}"
                        break

            return {
                "poster_path": f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}",
                "overview": movie.get("overview"),
                "tmdb_rating": round(movie.get("vote_average", 0), 1),
                "tmdb_id": movie_id,
                "trailer_url": trailer_url
            }

    return {}


# Ana Sayfa
@app.route("/")
def index():
    return render_template("index.html")


# Giriş
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        with driver.session() as session_db:
            result = session_db.run(
                "MATCH (u:User {name: $name}) RETURN u.password AS password",
                name=username
            )
            record = result.single()

            if record and record["password"] and check_password_hash(record["password"], password):
                session["username"] = username
                if username == "Admin":
                    return redirect("/admin")
                return redirect("/home")
            else:
                return render_template("login.html", error="Hatalı kullanıcı adı veya şifre.")
    return render_template("login.html")


# Kayıt
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        hashed_password = generate_password_hash(password)

        with driver.session() as session_db:
            result = session_db.run("MATCH (u:User {name: $name}) RETURN u", name=username)
            if result.single():
                return render_template("register.html", error="Bu kullanıcı zaten var.")
            else:
                session_db.run(
                    "CREATE (:User {name: $name, password: $password})",
                    name=username, password=hashed_password
                )
                session["username"] = username
                return redirect("/home")
    return render_template("register.html")


@app.route("/home")
def home():
    if "username" not in session:
        return redirect("/")

    username = session["username"]

    with driver.session() as session_db:
        # En son eklenen 5 film
        recent_movies_result = session_db.run("""
            MATCH (m:Movie)
            RETURN m.title AS title, m.year AS year
            ORDER BY m.year DESC
            LIMIT 5
        """)
        recent_movies = recent_movies_result.data()

        # Önerilen filmler (tekrarsız)
        recommendations_result = session_db.run("""
            MATCH (u:User {name: $name})-[:LIKES]->(:Movie)<-[:LIKES]-(other:User)-[:LIKES]->(rec:Movie)
            WHERE NOT (u)-[:LIKES]->(rec)
            RETURN DISTINCT rec.title AS title, rec.year AS year
            LIMIT 5
        """, name=username)
        recommendations = recommendations_result.data()

        # Zaten önerilen film başlıkları
        recommendation_titles = {film['title'] for film in recommendations}

        # Eğer öneri sayısı 5’ten azsa → popülerlerden tamamla, ama tekrar etme
        if len(recommendations) < 5:
            remaining = 5 - len(recommendations)
            popular_result = session_db.run("""
                MATCH (m:Movie)<-[:LIKES]-(:User)
                RETURN m.title AS title, m.year AS year, COUNT(*) AS likes
                ORDER BY likes DESC
            """)
            popular_movies_all = popular_result.data()

            for film in popular_movies_all:
                if film["title"] not in recommendation_titles:
                    recommendations.append(film)
                    recommendation_titles.add(film["title"])
                    if len(recommendations) == 5:
                        break

        # En çok beğenilen 5 film (ayrıca gösterilecek)
        popular_movies_result = session_db.run("""
            MATCH (m:Movie)<-[:LIKES]-(:User)
            RETURN m.title AS title, m.year AS year, COUNT(*) AS like_count
            ORDER BY like_count DESC
            LIMIT 5
        """)
        popular_movies = popular_movies_result.data()

        # Tüm filmleri getir
        all_movies_result = session_db.run("""
            MATCH (m:Movie)
            RETURN m.title AS title, m.year AS year
            ORDER BY m.title
        """)
        all_movies = all_movies_result.data()
        
        genres_result = session_db.run("""
            MATCH (g:Genre)
            RETURN g.name AS name
            ORDER BY g.name
        """)
        genres = genres_result.data()

    return render_template("home.html",
                           username=username,
                           recent_movies=recent_movies,
                           recommendations=recommendations,
                           popular_movies=popular_movies,
                           all_movies=all_movies,
                           genres=genres)

    

@app.route("/movie/<title>")
def movie_detail(title):
    if "username" not in session:
        return redirect("/")

    username = session["username"]

    with driver.session() as session_db:
        result = session_db.run("""
            MATCH (m:Movie {title: $title})
            RETURN m.title AS title, m.year AS year
        """, title=title)
        movie = result.single()
        if not movie:
            return render_template("not_found.html"), 404

        # Neo4j bilgileri
        genres_result = session_db.run("""
            MATCH (m:Movie {title: $title})-[:IN_GENRE]->(g:Genre)
            RETURN g.name AS genre
        """, title=title)
        genres = [record["genre"] for record in genres_result]

        like_result = session_db.run("""
            MATCH (u:User {name: $username}), (m:Movie {title: $title})
            RETURN EXISTS((u)-[:LIKES]->(m)) AS liked
        """, username=username, title=title)
        liked = like_result.single()["liked"]

        rating_result = session_db.run("""
            MATCH (u:User {name: $username})-[r:RATED]->(m:Movie {title: $title})
            RETURN r.rating AS rating, r.comment AS comment
        """, username=username, title=title)
        rating_data = rating_result.single()
        user_rating = rating_data["rating"] if rating_data else None
        user_comment = rating_data["comment"] if rating_data else None

        comments_result = session_db.run("""
            MATCH (u:User)-[r:RATED]->(m:Movie {title: $title})
            WHERE r.comment IS NOT NULL AND r.comment <> ""
            RETURN u.name AS username, r.rating AS rating, r.comment AS comment
            ORDER BY r.rating DESC
        """, title=title)
        comments = comments_result.data()

    # TMDb bilgilerini çek
    tmdb_data = get_movie_details_from_tmdb(title)

    return render_template("movie_detail.html",
                           movie=movie,
                           liked=liked,
                           user_rating=user_rating,
                           user_comment=user_comment,
                           comments=comments,
                           genres=genres,
                           tmdb=tmdb_data)


@app.route("/like/<title>", methods=["POST"])
def like_movie(title):
    if "username" not in session:
        return redirect("/")

    username = session["username"]
    action = request.form.get("action")

    with driver.session() as session_db:
        if action == "like":
            session_db.run("""
                MATCH (u:User {name: $username}), (m:Movie {title: $title})
                MERGE (u)-[:LIKES]->(m)
            """, username=username, title=title)
        elif action == "unlike":
            session_db.run("""
                MATCH (u:User {name: $username})-[r:LIKES]->(m:Movie {title: $title})
                DELETE r
            """, username=username, title=title)

    return redirect(f"/movie/{title}")


@app.route("/rate/<title>", methods=["POST"])
def rate_movie(title):
    if "username" not in session:
        return redirect("/")

    username = session["username"]
    rating = request.form.get("rating")
    comment = request.form.get("comment")

    with driver.session() as session_db:
        # Var olan yorum varsa güncelle, yoksa oluştur
        result = session_db.run("""
            MATCH (u:User {name: $username})-[r:RATED]->(m:Movie {title: $title})
            RETURN r
        """, username=username, title=title)

        if result.single():
            # Güncelleme
            session_db.run("""
                MATCH (u:User {name: $username})-[r:RATED]->(m:Movie {title: $title})
                SET r.rating = toInteger($rating), r.comment = $comment
            """, username=username, title=title, rating=rating, comment=comment)
        else:
            # Yeni oluştur
            session_db.run("""
                MATCH (u:User {name: $username}), (m:Movie {title: $title})
                CREATE (u)-[:RATED {rating: toInteger($rating), comment: $comment}]->(m)
            """, username=username, title=title, rating=rating, comment=comment)
    return redirect(f"/movie/{title}")


@app.route("/delete_rating/<title>", methods=["POST"])
def delete_rating(title):
    if "username" not in session:
        return redirect("/")

    username = session["username"]

    with driver.session() as session_db:
        session_db.run("""
            MATCH (u:User {name: $username})-[r:RATED]->(m:Movie {title: $title})
            DELETE r
        """, username=username, title=title)

    return redirect(f"/movie/{title}")


@app.route("/genre/<genre_name>")
def genre_movies(genre_name):
    if "username" not in session:
        return redirect("/")

    with driver.session() as session_db:
        result = session_db.run("""
            MATCH (m:Movie)-[:IN_GENRE]->(g:Genre {name: $genre_name})
            RETURN m.title AS title, m.year AS year
            ORDER BY m.title
        """, genre_name=genre_name)
        movies = result.data()

    return render_template("genre_movies.html", genre_name=genre_name, movies=movies)



@app.route("/profile")
def profile():
    if "username" not in session:
        return redirect("/")

    username = session["username"]

    with driver.session() as session_db:
        # Beğenilen filmler
        liked_movies_result = session_db.run("""
            MATCH (u:User {name: $username})-[:LIKES]->(m:Movie)
            RETURN m.title AS title, m.year AS year
            ORDER BY m.title
        """, username=username)
        liked_movies = liked_movies_result.data()

        # Arkadaşlar
        friends_result = session_db.run("""
        MATCH (u:User {name: $username})-[:FRIEND]-(f:User)
        RETURN DISTINCT f.name AS name
        ORDER BY f.name
        """, username=username)
        friends = [record["name"] for record in friends_result]

        # Diğer kullanıcılar (kendisi, Admin ve arkadaşları hariç)
        other_users_result = session_db.run("""
            MATCH (other:User)
            WHERE other.name <> $username AND other.name <> 'Admin' AND NOT (other.name IN $friends)
            RETURN other.name AS name
            ORDER BY other.name
        """, username=username, friends=friends)
        other_users = [record["name"] for record in other_users_result]

    return render_template("profile.html", username=username, liked_movies=liked_movies,
                           friends=friends, other_users=other_users)
    

@app.route("/add_friend/<username>", methods=["POST"])
def add_friend(username):
    current_user = session.get("username")
    if not current_user:
        return redirect("/login")

    with driver.session() as session_db:
        result = session_db.run("""
            MATCH (a:User {name: $current_user})-[:FRIEND]-(b:User {name: $target})
            RETURN b
        """, current_user=current_user, target=username)

        if not result.single():
            session_db.run("""
                MATCH (a:User {name: $current_user}), (b:User {name: $target})
                CREATE (a)-[:FRIEND]->(b),
                       (b)-[:FRIEND]->(a)
            """, current_user=current_user, target=username)

    return redirect("/profile")


@app.route("/remove_friend/<username>", methods=["POST"])
def remove_friend(username):
    current_user = session.get("username")
    if not current_user:
        return redirect("/login")

    with driver.session() as session_db:
        session_db.run("""
            MATCH (a:User {name: $current_user})-[r1:FRIEND]->(b:User {name: $target})
            DELETE r1
        """, current_user=current_user, target=username)

        session_db.run("""
            MATCH (a:User {name: $target})-[r2:FRIEND]->(b:User {name: $current_user})
            DELETE r2
        """, current_user=current_user, target=username)

    return redirect("/profile")


#Admin Paneli

@app.route("/admin")
def admin_panel():
    if session.get("username") != "Admin":
        return redirect("/")

    with driver.session() as session_db:
        movies_result = session_db.run("""
            MATCH (m:Movie)
            RETURN m.title AS title, m.year AS year
            ORDER BY m.title
        """)
        movies = movies_result.data()

        users_result = session_db.run("""
            MATCH (u:User)
            RETURN u.name AS name
            ORDER BY u.name
        """)
        users = users_result.data()

        genres_result = session_db.run("""
            MATCH (g:Genre)
            RETURN g.name AS name
            ORDER BY g.name
        """)
        genres = [record["name"] for record in genres_result]

    return render_template("admin.html", movies=movies, users=users, genres=genres)


@app.route("/admin/add_movie", methods=["POST"])
def add_movie():
    if session.get("username") != "Admin":
        return redirect("/")

    title = request.form["title"]
    year = int(request.form["year"])
    genre = request.form["genre"]

    with driver.session() as session_db:
        session_db.run("""
            MERGE (m:Movie {title: $title})
            SET m.year = $year
            MERGE (g:Genre {name: $genre})
            MERGE (m)-[:IN_GENRE]->(g)
        """, title=title, year=year, genre=genre)

    return redirect("/admin")


@app.route("/admin/comments")
def admin_comments():
    if session.get("username") != "Admin":
        return redirect("/")

    with driver.session() as session_db:
        results = session_db.run("""
            MATCH (u:User)-[r:RATED]->(m:Movie)
            RETURN elementId(r) AS rel_id, u.name AS username, m.title AS movie_title,
                   r.rating AS rating, r.comment AS comment
            ORDER BY m.title
        """)
        comments = []
        
        for record in results:
            comments.append({
                "rel_id": record["rel_id"],
                "username": record["username"],
                "movie_title": record["movie_title"],
                "rating": record["rating"],
                "comment": record["comment"]
            })

    return render_template("admin_comments.html", comments=comments)


@app.route("/admin/delete_comment/<rel_id>", methods=["POST"])
def delete_comment(rel_id):
    if session.get("username") != "Admin":
        return redirect("/")

    with driver.session() as session_db:
        session_db.run("""
            MATCH ()-[r:RATED]->()
            WHERE elementId(r) = $rel_id
            DELETE r
        """, rel_id=rel_id)

    return redirect("/admin/comments")



@app.route("/admin/delete_movie/<title>", methods=["POST"])
def delete_movie(title):
    if session.get("username") != "Admin":
        return redirect("/")

    with driver.session() as session_db:
        session_db.run("""
            MATCH (m:Movie {title: $title})
            DETACH DELETE m
        """, title=title)

    return redirect("/admin")


@app.route("/admin/delete_user/<username>", methods=["POST"])
def delete_user(username):
    if session.get("username") != "Admin":
        return redirect("/")

    with driver.session() as session_db:
        session_db.run("""
            MATCH (u:User {name: $username})
            DETACH DELETE u
        """, username=username)

    return redirect("/admin")



@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)